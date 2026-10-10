"""Single-owner fitness tracker. WSGI application, served by Gunicorn in production."""

import csv
import logging
import zipfile
from xml.sax.saxutils import escape as xml_escape
import hashlib
import hmac
import io
import json
import math
import os
import re
import secrets
import sqlite3
import time
from datetime import date, timedelta
from http.cookies import SimpleCookie
from pathlib import Path

os.umask(0o077)
BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("DATA_DIR", BASE / "data"))
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://127.0.0.1:8080").rstrip("/")
SECURE_COOKIE = os.environ.get("SECURE_COOKIE", "true").lower() == "true"
PLAN = json.loads((BASE / "plan.json").read_text())
COOKIE = "fitness_session"
MAX_BODY = 32_768
STATES = {"", "完成", "部分", "未完成"}


class HTTPError(Exception):
    def __init__(self, status, message, extra=None):
        self.status, self.message, self.extra = status, message, extra or {}


class Database(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def connection():
    DATA.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATA / "fitness.sqlite3", timeout=10, factory=Database)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


def init_db():
    with connection() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS owner(id INTEGER PRIMARY KEY CHECK(id=1),username TEXT NOT NULL,password TEXT NOT NULL,profile TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,csrf TEXT NOT NULL,expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS records(day TEXT PRIMARY KEY,data TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS analyses(id TEXT PRIMARY KEY,start TEXT,end TEXT,created REAL,model TEXT,result TEXT,snapshot TEXT);
        CREATE TABLE IF NOT EXISTS previews(id TEXT PRIMARY KEY,session TEXT,expires REAL,payload TEXT,config_hash TEXT);
        CREATE TABLE IF NOT EXISTS llm_calls(id TEXT PRIMARY KEY,created REAL);
        CREATE TABLE IF NOT EXISTS login_attempts(bucket TEXT PRIMARY KEY,count INTEGER NOT NULL,start REAL NOT NULL);
        """)


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(
        password.encode(),
        salt=bytes.fromhex(salt),
        n=32768,
        r=8,
        p=1,
        maxmem=64 * 1024 * 1024,
    ).hex()
    return salt + ":" + digest


def password_ok(password, stored):
    salt, _ = stored.split(":", 1)
    return hmac.compare_digest(password_hash(password, salt), stored)


def create_owner(username, password):
    if not re.fullmatch(r"[A-Za-z0-9_-]{2,32}", username):
        raise ValueError("用户名须为2～32位字母、数字、下划线或短横线。")
    if not 12 <= len(password) <= 256:
        raise ValueError("密码须为12～256个字符。")
    with connection() as db:
        if db.execute("SELECT 1 FROM owner").fetchone():
            raise ValueError("账户已存在，请使用修改密码命令。")
        db.execute(
            "INSERT INTO owner VALUES(1,?,?,?)",
            (
                username,
                password_hash(password),
                json.dumps(
                    {
                        "startDate": date.today().isoformat(),
                        "startWeight": None,
                        "targetWeight": None,
                        "days": 90,
                        "stepGoal": None,
                        "kcalGoal": None,
                        "proteinGoal": None,
                    }
                ),
            ),
        )


def set_password(password):
    if not 12 <= len(password) <= 256:
        raise ValueError("密码须为12～256个字符。")
    with connection() as db:
        if not db.execute("SELECT 1 FROM owner").fetchone():
            raise ValueError("请先创建账户。")
        db.execute("UPDATE owner SET password=?", (password_hash(password),))
        db.execute("DELETE FROM sessions")


def read_json(env):
    if not env.get("CONTENT_TYPE", "").split(";")[0].strip() == "application/json":
        raise HTTPError(415, "请求格式应为JSON。")
    try:
        length = int(env.get("CONTENT_LENGTH") or 0)
    except ValueError:
        raise HTTPError(400, "请求长度无效。")
    if not 0 < length <= MAX_BODY:
        raise HTTPError(413, "请求为空或过大。")
    try:
        data = json.loads(env["wsgi.input"].read(length))
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except (ValueError, UnicodeError):
        raise HTTPError(400, "请求内容无效。")


def require_origin(env):
    if env.get("HTTP_ORIGIN") != PUBLIC_URL:
        raise HTTPError(403, "访问来源不匹配，请从配置的网页地址打开。")


def get_session(env, db):
    cookies = SimpleCookie()
    try:
        cookies.load(env.get("HTTP_COOKIE", ""))
    except Exception:
        raise HTTPError(401, "请重新登录。")
    raw = cookies.get(COOKIE)
    if not raw:
        raise HTTPError(401, "请先登录。")
    token = hashlib.sha256(raw.value.encode()).hexdigest()
    session = db.execute(
        "SELECT * FROM sessions WHERE token=? AND expires>?", (token, time.time())
    ).fetchone()
    if not session:
        raise HTTPError(401, "登录已过期，请重新登录。")
    return session


def require_csrf(env, session):
    require_origin(env)
    if not hmac.compare_digest(env.get("HTTP_X_CSRF_TOKEN", ""), session["csrf"]):
        raise HTTPError(403, "页面已失效，请刷新后再试。")


def profile(db):
    row = db.execute("SELECT profile FROM owner WHERE id=1").fetchone()
    if not row:
        raise HTTPError(503, "请先在服务器终端创建登录账户。")
    return json.loads(row["profile"])


def records(db):
    return {
        r["day"]: {
            "data": json.loads(r["data"]),
            "revision": r["revision"],
            "updated": r["updated"],
        }
        for r in db.execute("SELECT * FROM records ORDER BY day")
    }


def number(value, low, high, whole=False):
    if value is None or value == "":
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (float, int))
        or not math.isfinite(value)
        or not low <= value <= high
        or (whole and int(value) != value)
    ):
        raise HTTPError(400, "数值超出可记录范围。")
    return value


def day_plan(day, p):
    try:
        selected = date.fromisoformat(day)
        start = date.fromisoformat(p["startDate"])
    except (ValueError, TypeError):
        raise HTTPError(400, "日期无效。")
    if selected.isoformat() != day:
        raise HTTPError(400, "日期请使用 YYYY-MM-DD 格式。")
    index = (selected - start).days
    if not 0 <= index < 90:
        raise HTTPError(400, "日期超出90天计划。")
    return PLAN["days"][index]


def valid_patch(patch, day):
    if not isinstance(patch, dict) or not patch:
        raise HTTPError(400, "没有可保存的修改。")
    out = {}
    numeric = {
        "weight": (40, 150, False),
        "waist": (40, 200, False),
        "steps": (0, 60000, True),
        "sleep": (0, 24, False),
    }
    for key, value in patch.items():
        if key in numeric:
            out[key] = number(value, *numeric[key])
        elif key in {"breakfast", "lunch", "dinner", "snack"}:
            if value not in STATES:
                raise HTTPError(400, "餐食打卡状态无效。")
            out[key] = value
        elif key == "workout":
            allowed = STATES | (
                {"计划休息"} if day["trainingType"] in {"恢复", "休息"} else set()
            )
            if value not in allowed:
                raise HTTPError(400, "当天安排训练，不能用计划休息代替。")
            out[key] = value
        elif key == "fatigue":
            if value not in {"", "轻松", "正常", "明显疲劳", "疼痛／不适"}:
                raise HTTPError(400, "疲劳选项无效。")
            out[key] = value
        elif key in {"cardioMinutes", "cardioDistance"}:
            out[key] = number(value, 0, 1440 if key == "cardioMinutes" else 200)
        elif key == "mealsActual":
            if not isinstance(value, dict) or set(value) - {
                "breakfast",
                "lunch",
                "dinner",
                "snack",
            }:
                raise HTTPError(400, "餐食记录格式无效。")
            if any(not isinstance(v, str) or len(v) > 500 for v in value.values()):
                raise HTTPError(400, "每餐描述最多500字。")
            out[key] = value
        elif key == "notes":
            if not isinstance(value, str) or len(value) > 2000:
                raise HTTPError(400, "备注最多2000字。")
            out[key] = value
        elif key == "actions":
            ids = {a["id"] for a in day["actions"]}
            if not isinstance(value, dict) or set(value) - ids:
                raise HTTPError(400, "训练项目无效。")
            clean = {}
            for aid, a in value.items():
                if not isinstance(a, dict) or set(a) - {
                    "done",
                    "weight",
                    "reps",
                    "sets",
                }:
                    raise HTTPError(400, "训练记录格式无效。")
                clean[aid] = {}
                if "done" in a:
                    if not isinstance(a["done"], bool):
                        raise HTTPError(400, "训练打卡无效。")
                    clean[aid]["done"] = a["done"]
                if "weight" in a:
                    clean[aid]["weight"] = number(a["weight"], 0, 400)
                if "sets" in a:
                    clean[aid]["sets"] = number(a["sets"], 0, 30, True)
                if "reps" in a:
                    if not isinstance(a["reps"], str) or len(a["reps"]) > 100:
                        raise HTTPError(400, "各组次数最多100字。")
                    clean[aid]["reps"] = a["reps"]
            out[key] = clean
        else:
            raise HTTPError(400, "包含不支持的记录字段。")
    return out


def handle(env):
    path, method = env.get("PATH_INFO", "/"), env.get("REQUEST_METHOD", "GET")
    if method == "GET" and path == "/api/health":
        return 200, {"ok": True}, []
    static = {
        "/": "index.html",
        "/app.js": "app.js",
        "/style.css": "style.css",
        "/icon.svg": "icon.svg",
        "/manifest.webmanifest": "manifest.webmanifest",
    }
    if method == "GET" and path in static:
        content = (BASE / "static" / static[path]).read_bytes()
        mime = {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".svg": "image/svg+xml",
            ".webmanifest": "application/manifest+json",
        }[Path(static[path]).suffix]
        return 200, content, [("Content-Type", mime)]
    with connection() as db:
        if path == "/api/login" and method == "POST":
            require_origin(env)
            data = read_json(env)
            username, password = data.get("username"), data.get("password")
            if (
                not isinstance(username, str)
                or not isinstance(password, str)
                or len(username) > 32
                or len(password) > 256
            ):
                raise HTTPError(400, "请输入用户名和密码。")
            now = time.time()
            bucket = hashlib.sha256(
                (env.get("REMOTE_ADDR", "unknown") + "|" + username.lower()).encode()
            ).hexdigest()
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM login_attempts WHERE start<?", (now - 900,))
            attempt = db.execute(
                "SELECT * FROM login_attempts WHERE bucket=?", (bucket,)
            ).fetchone()
            if attempt and now - attempt["start"] < 900 and attempt["count"] >= 8:
                raise HTTPError(429, "尝试次数过多，请15分钟后重试。")
            owner = db.execute("SELECT * FROM owner WHERE id=1").fetchone()
            stored = owner["password"] if owner else DUMMY_PASSWORD
            correct = password_ok(password, stored)
            if (
                not owner
                or not correct
                or not hmac.compare_digest(
                    username.encode(), owner["username"].encode()
                )
            ):
                count = (
                    attempt["count"] + 1
                    if attempt and now - attempt["start"] < 900
                    else 1
                )
                start = (
                    attempt["start"]
                    if attempt and now - attempt["start"] < 900
                    else now
                )
                db.execute(
                    "INSERT OR REPLACE INTO login_attempts VALUES(?,?,?)",
                    (bucket, count, start),
                )
                db.commit()
                raise HTTPError(401, "用户名或密码不正确。")
            db.execute("DELETE FROM login_attempts WHERE bucket=?", (bucket,))
            db.execute("DELETE FROM sessions WHERE expires<?", (now,))
            raw, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            db.execute(
                "INSERT INTO sessions VALUES(?,?,?)",
                (hashlib.sha256(raw.encode()).hexdigest(), csrf, now + 30 * 86400),
            )
            cookie = (
                f"{COOKIE}={raw}; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000"
                + ("; Secure" if SECURE_COOKIE else "")
            )
            return 200, {"ok": True, "csrf": csrf}, [("Set-Cookie", cookie)]
        session = get_session(env, db)
        if path == "/api/state" and method == "GET":
            return (
                200,
                {
                    "profile": profile(db),
                    "plan": PLAN,
                    "llm": llm.public_config(DATA),
                    "analyses": llm.history(db),
                    "records": records(db),
                    "csrf": session["csrf"],
                },
                [],
            )
        if path == "/api/logout" and method == "POST":
            require_csrf(env, session)
            db.execute("DELETE FROM sessions WHERE token=?", (session["token"],))
            return (
                200,
                {"ok": True},
                [
                    (
                        "Set-Cookie",
                        f"{COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
                        + ("; Secure" if SECURE_COOKIE else ""),
                    )
                ],
            )
        if path == "/api/profile" and method == "PATCH":
            require_csrf(env, session)
            data = read_json(env)
            if not {"startDate", "startWeight", "targetWeight"} <= set(data) or set(
                data
            ) - {
                "startDate",
                "startWeight",
                "targetWeight",
                "stepGoal",
                "kcalGoal",
                "proteinGoal",
            }:
                raise HTTPError(400, "计划设置格式无效。")
            try:
                data["startDate"] = date.fromisoformat(data["startDate"]).isoformat()
            except (TypeError, ValueError):
                raise HTTPError(400, "开始日期无效。")
            start = number(data["startWeight"], 40, 150)
            target = number(data["targetWeight"], 40, 150)
            if target is not None and start is None:
                raise HTTPError(400, "填写目标前请先设置起始体重。")
            p = {
                "startDate": data["startDate"],
                "startWeight": start,
                "targetWeight": target,
                "days": 90,
                "stepGoal": number(data.get("stepGoal"), 0, 60000, True),
                "kcalGoal": number(data.get("kcalGoal"), 0, 10000, True),
                "proteinGoal": number(data.get("proteinGoal"), 0, 500),
            }
            db.execute("UPDATE owner SET profile=? WHERE id=1", (json.dumps(p),))
            return 200, {"profile": p}, []
        if path.startswith("/api/records/") and method == "PATCH":
            require_csrf(env, session)
            data = read_json(env)
            selected = path.removeprefix("/api/records/")
            p = profile(db)
            dp = day_plan(selected, p)
            patch = valid_patch(data.get("patch"), dp)
            rev = data.get("revision")
            if not isinstance(rev, int) or isinstance(rev, bool) or rev < 0:
                raise HTTPError(400, "记录版本无效。")
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM records WHERE day=?", (selected,)
            ).fetchone()
            current = json.loads(row["data"]) if row else {}
            current_rev = row["revision"] if row else 0
            if rev != current_rev:
                raise HTTPError(
                    409,
                    "另一个页面更新了记录，已读取最新内容，请重新提交你的修改。",
                    {"record": {"data": current, "revision": current_rev}},
                )
            for key, value in patch.items():
                if key == "actions":
                    for aid, action in value.items():
                        current.setdefault("actions", {}).setdefault(aid, {}).update(
                            action
                        )
                elif key == "mealsActual":
                    current.setdefault(key, {}).update(value)
                else:
                    current[key] = value
            now = time.time()
            record = {"data": current, "revision": current_rev + 1, "updated": now}
            db.execute(
                "INSERT OR REPLACE INTO records VALUES(?,?,?,?)",
                (
                    selected,
                    json.dumps(current, ensure_ascii=False),
                    current_rev + 1,
                    now,
                ),
            )
            return 200, {"record": record}, []
        if path.startswith("/api/llm/"):
            require_csrf(env, session)
            return llm.handle(
                path,
                method,
                read_json(env),
                db,
                session,
                DATA,
                profile(db),
                PLAN,
                HTTPError,
            )
        if method == "GET" and path == "/api/export.json":
            payload = json.dumps(
                {
                    "version": 1,
                    "profile": profile(db),
                    "records": records(db),
                    "analyses": llm.history(db),
                },
                ensure_ascii=False,
                indent=2,
            ).encode()
            return (
                200,
                payload,
                [
                    ("Content-Type", "application/json; charset=utf-8"),
                    (
                        "Content-Disposition",
                        'attachment; filename="fitness-records.json"',
                    ),
                ],
            )
        if method == "GET" and path == "/api/export.csv":
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(
                [
                    "日期",
                    "晨重kg",
                    "腰围cm",
                    "步数",
                    "睡眠小时",
                    "疲劳",
                    "早餐",
                    "午餐",
                    "晚餐",
                    "加餐",
                    "训练",
                    "备注",
                    "动作记录",
                    "实际餐食",
                    "有氧分钟",
                    "有氧公里",
                ]
            )
            for day, r in records(db).items():
                d = r["data"]
                values = (
                    [day]
                    + [
                        d.get(k, "")
                        for k in [
                            "weight",
                            "waist",
                            "steps",
                            "sleep",
                            "fatigue",
                            "breakfast",
                            "lunch",
                            "dinner",
                            "snack",
                            "workout",
                            "notes",
                        ]
                    ]
                    + [
                        json.dumps(d.get("actions", {}), ensure_ascii=False),
                        json.dumps(d.get("mealsActual", {}), ensure_ascii=False),
                        d.get("cardioMinutes", ""),
                        d.get("cardioDistance", ""),
                    ]
                )
                writer.writerow(
                    [
                        (
                            "'" + v
                            if isinstance(v, str)
                            and (
                                v.lstrip().startswith(("=", "+", "-", "@"))
                                or v.startswith(("\t", "\r", "\n"))
                            )
                            else v
                        )
                        for v in values
                    ]
                )
            return (
                200,
                ("\ufeff" + buf.getvalue()).encode(),
                [
                    ("Content-Type", "text/csv; charset=utf-8"),
                    (
                        "Content-Disposition",
                        'attachment; filename="fitness-records.csv"',
                    ),
                ],
            )
        if method == "GET" and path == "/api/plan.xlsx":
            return (
                200,
                plan_xlsx(profile(db)),
                [
                    (
                        "Content-Type",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    ),
                    ("Content-Disposition", 'attachment; filename="fitness-plan.xlsx"'),
                ],
            )
    raise HTTPError(404, "页面不存在。")


def application(env, start_response):
    try:
        code, body, headers = handle(env)
    except HTTPError as e:
        code, body, headers = e.status, {"error": e.message, **e.extra}, []
    except Exception:
        # Never include request bodies, cookies, passwords or private records in logs.
        logging.error(
            "Request failed (%s)", type(__import__("sys").exception()).__name__
        )
        code, body, headers = 500, {"error": "保存遇到问题，请稍后重试。"}, []
    if not isinstance(body, bytes):
        body = json.dumps(body, ensure_ascii=False).encode()
        headers.append(("Content-Type", "application/json; charset=utf-8"))
    headers.extend(
        [
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "same-origin"),
            ("X-Frame-Options", "DENY"),
            (
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            ),
        ]
    )
    statuses = {
        200: "OK",
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        409: "Conflict",
        413: "Payload Too Large",
        415: "Unsupported Media Type",
        429: "Too Many Requests",
        500: "Internal Server Error",
        503: "Service Unavailable",
    }
    start_response(f'{code} {statuses.get(code,"Error")}', headers)
    return [body]


import llm


def plan_xlsx(p):
    rows = [["日期", "训练类型", "动作", "计划工作组", "计划次数", "说明"]]
    for i, day in enumerate(PLAN["days"]):
        for a in day["actions"] or [
            {"name": "休息", "sets": 0, "reps": "", "tip": "按恢复情况安排"}
        ]:
            rows.append(
                [
                    (
                        date.fromisoformat(p["startDate"]) + timedelta(days=i)
                    ).isoformat(),
                    day["trainingType"],
                    a["name"],
                    str(a["sets"]),
                    a["reps"],
                    a["tip"],
                ]
            )
    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        + "".join(
            "<row>"
            + "".join(
                '<c t="inlineStr"><is><t>' + xml_escape(str(v)) + "</t></is></c>"
                for v in row
            )
            + "</row>"
            for row in rows
        )
        + "</sheetData></worksheet>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        )
        z.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        )
        z.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="通用计划" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


init_db()
DUMMY_PASSWORD = password_hash(secrets.token_urlsafe(24))

if __name__ == "__main__":
    from wsgiref.simple_server import make_server, WSGIRequestHandler

    class QuietHandler(WSGIRequestHandler):
        def log_message(self, *args):
            pass

    print("Local preview: http://127.0.0.1:8080", flush=True)
    with make_server(
        "127.0.0.1", 8080, application, handler_class=QuietHandler
    ) as server:
        server.serve_forever()
