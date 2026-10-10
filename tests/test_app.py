"""Only temporary databases and a loopback mock API are used."""

import concurrent.futures
import io
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

TEMP = tempfile.TemporaryDirectory()
os.environ["DATA_DIR"] = TEMP.name
os.environ["PUBLIC_URL"] = "http://127.0.0.1:8080"
os.environ["SECURE_COOKIE"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import llm


class Mock(BaseHTTPRequestHandler):
    requests = []
    mode = "ok"

    def log_message(self, *args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.requests.append((self.path, payload, self.headers.get("Authorization")))
        if self.mode == "error":
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"provider-private-error")
            return
        if self.mode == "invalid":
            raw = b"no json"
        elif self.mode == "large":
            raw = b"x" * 128001
        else:
            result = (
                "【模拟分析】餐食未记录，营养无法估算。训练重量和组数按实际记录，关注多日趋势。 <script>window.injected=1</script> "
                + self.headers.get("Authorization", "")
            )
            raw = json.dumps({"choices": [{"message": {"content": result}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class Client:
    def __init__(self):
        self.cookie = ""
        self.csrf = ""

    def request(self, path, method="GET", body=None, origin=None, csrf=None):
        raw = json.dumps(body).encode() if body is not None else b""
        env = {
            "PATH_INFO": path,
            "REQUEST_METHOD": method,
            "wsgi.input": io.BytesIO(raw),
            "CONTENT_LENGTH": str(len(raw)),
            "CONTENT_TYPE": "application/json",
            "REMOTE_ADDR": "127.0.0.1",
            "HTTP_ORIGIN": origin or app.PUBLIC_URL,
            "HTTP_COOKIE": self.cookie,
            "HTTP_X_CSRF_TOKEN": self.csrf if csrf is None else csrf,
        }
        headers = {}
        status = []

        def response(s, h):
            status.append(int(s.split()[0]))
            headers.update(h)

        data = b"".join(app.application(env, response))
        if "Set-Cookie" in headers:
            self.cookie = headers["Set-Cookie"].split(";")[0]
        if headers.get("Content-Type", "").startswith("application/json"):
            data = json.loads(data)
            if isinstance(data, dict) and data.get("csrf"):
                self.csrf = data["csrf"]
        return status[0], data, headers


class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Mock)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/v1"
        os.environ["LLM_TEST_ENDPOINT"] = cls.url

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        TEMP.cleanup()

    def setUp(self):
        app.DATA = Path(TEMP.name) / secrets.token_hex(6)
        app.init_db()
        self.password = secrets.token_urlsafe(20)
        app.create_owner("demo_owner", self.password)
        self.client = Client()
        self.assertEqual(
            self.client.request(
                "/api/login",
                "POST",
                {"username": "demo_owner", "password": self.password},
            )[0],
            200,
        )
        self.day = date.today().isoformat()
        Mock.requests.clear()
        Mock.mode = "ok"
        self.key = secrets.token_urlsafe(32)

    def setup_llm(self):
        status, data, _ = self.client.request(
            "/api/llm/config",
            "PUT",
            {
                "provider": "Local mock",
                "baseUrl": self.url,
                "model": "mock-model",
                "key": self.key,
            },
        )
        self.assertEqual(status, 200, data)
        return data

    def preview(self, notes=False, days=1):
        end = (date.today() + timedelta(days=days - 1)).isoformat()
        status, data, _ = self.client.request(
            "/api/llm/preview",
            "POST",
            {"start": self.day, "end": end, "includeNotes": notes},
        )
        self.assertEqual(status, 200, data)
        return data

    def test_authentication_and_single_owner(self):
        anon = Client()
        for path in [
            "/api/state",
            "/api/export.json",
            "/api/export.csv",
            "/api/plan.xlsx",
        ]:
            self.assertEqual(anon.request(path)[0], 401)
        for path in [
            "/api/llm/config",
            "/api/llm/preview",
            "/api/llm/test",
            "/api/llm/analyze",
        ]:
            self.assertEqual(anon.request(path, "POST", {})[0], 401)
        with self.assertRaises(ValueError):
            app.create_owner("second", secrets.token_urlsafe(20))
        self.assertEqual(
            anon.request(
                "/api/login", "POST", {"username": "second", "password": self.password}
            )[0],
            401,
        )
        self.assertEqual(
            self.client.request("/api/llm/config", "PUT", {}, csrf="bad")[0], 403
        )
        self.assertEqual(
            self.client.request(
                "/api/profile", "PATCH", {}, origin="https://evil.example"
            )[0],
            403,
        )
        app.set_password(secrets.token_urlsafe(20))
        self.assertEqual(self.client.request("/api/state")[0], 401)

    def test_login_rate_limit(self):
        c = Client()
        for _ in range(8):
            self.assertEqual(
                c.request(
                    "/api/login",
                    "POST",
                    {"username": "demo_owner", "password": "incorrect"},
                )[0],
                401,
            )
        self.assertEqual(
            c.request(
                "/api/login",
                "POST",
                {"username": "demo_owner", "password": "incorrect"},
            )[0],
            429,
        )

    def test_fresh_profile_no_fake_weight_and_goals(self):
        data = self.client.request("/api/state")[1]
        self.assertIsNone(data["profile"]["startWeight"])
        self.assertIsNone(data["profile"]["targetWeight"])
        for target in [None, 65, 75]:
            self.assertEqual(
                self.client.request(
                    "/api/profile",
                    "PATCH",
                    {"startDate": self.day, "startWeight": 65, "targetWeight": target},
                )[0],
                200,
            )
        self.assertEqual(
            self.client.request(
                "/api/profile",
                "PATCH",
                {"startDate": "invalid", "startWeight": None, "targetWeight": None},
            )[0],
            400,
        )

    def test_cas_actual_fields_persistence_export(self):
        patch_data = {
            "weight": 68.4,
            "actions": {
                "ex-0-0": {"done": True, "weight": 12, "sets": 2, "reps": "10 / 9"}
            },
            "mealsActual": {"breakfast": "虚构燕麦40g"},
            "notes": "=malicious()",
            "cardioMinutes": 20,
            "cardioDistance": 1.2,
        }
        self.assertEqual(
            self.client.request(
                "/api/records/" + self.day,
                "PATCH",
                {"revision": 0, "patch": patch_data},
            )[0],
            200,
        )
        self.assertEqual(
            self.client.request(
                "/api/records/" + self.day,
                "PATCH",
                {"revision": 0, "patch": {"sleep": 7}},
            )[0],
            409,
        )
        status, data, _ = self.client.request("/api/export.json")
        self.assertEqual(status, 200)
        self.assertEqual(data["records"][self.day]["data"], patch_data)
        csv = self.client.request("/api/export.csv")[1].decode()
        self.assertIn("'=malicious()", csv)
        self.assertIn("虚构燕麦40g", csv)
        status, raw, _ = self.client.request("/api/plan.xlsx")
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            self.assertIn(self.day, z.read("xl/worksheets/sheet1.xml").decode())
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import app; print(len(app.records(app.connection())))",
            ],
            cwd=app.BASE,
            env=dict(os.environ, DATA_DIR=str(app.DATA)),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "1")

    def test_csv_whitespace_formula_protection(self):
        for revision, note in enumerate([" =1+1", "\n=1+1", "\t@SUM(1,2)"]):
            status, _, _ = self.client.request(
                "/api/records/" + self.day,
                "PATCH",
                {"revision": revision, "patch": {"notes": note}},
            )
            self.assertEqual(status, 200)
            csv_text = self.client.request("/api/export.csv")[1].decode()
            self.assertIn("'" + note, csv_text)

    def test_cas_concurrent_save(self):
        def save(v):
            return self.client.request(
                "/api/records/" + self.day,
                "PATCH",
                {"revision": 0, "patch": {"steps": v}},
            )[0]

        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            statuses = list(pool.map(save, [100, 200]))
        self.assertEqual(sorted(statuses), [200, 409])

    def test_validation(self):
        for data in [
            {"steps": True},
            {"sleep": 25},
            {"weight": float("nan")},
            {"actions": {"unknown": {"done": True}}},
            {"actions": {"ex-0-0": {"sets": 2.5}}},
            {"mealsActual": {"breakfast": "a" * 501}},
            {"cardioDistance": 201},
        ]:
            self.assertEqual(
                self.client.request(
                    "/api/records/" + self.day, "PATCH", {"revision": 0, "patch": data}
                )[0],
                400,
                data,
            )

    def test_credentials_not_exposed(self):
        result = self.setup_llm()
        self.assertNotIn(self.key, json.dumps(result))
        self.assertTrue(result["llm"]["hasKey"])
        self.assertEqual((app.DATA / "llm-config.json").stat().st_mode & 0o777, 0o600)
        for path in [
            "/llm-config.json",
            "/data/llm-config.json",
            "/.env",
            "/../app.py",
        ]:
            self.assertEqual(self.client.request(path)[0], 404)
        self.assertNotIn(self.key, json.dumps(self.client.request("/api/state")[1]))
        self.assertNotIn(
            self.key, json.dumps(self.client.request("/api/export.json")[1])
        )
        self.assertEqual(self.client.request("/api/llm/config", "DELETE", {})[0], 200)
        self.assertFalse((app.DATA / "llm-config.json").exists())

    def test_key_retention_and_endpoint_change(self):
        self.setup_llm()
        payload = {
            "provider": "Mock",
            "baseUrl": self.url,
            "model": "mock-2",
            "key": "",
        }
        self.assertEqual(self.client.request("/api/llm/config", "PUT", payload)[0], 200)
        self.assertEqual(llm.config(app.DATA)["key"], self.key)
        payload["baseUrl"] = "https://api.openai.com/v1"
        self.assertEqual(self.client.request("/api/llm/config", "PUT", payload)[0], 400)
        self.assertEqual(llm.config(app.DATA)["baseUrl"], self.url)

    def test_cookie_headers_and_noncanonical_date(self):
        previous = app.SECURE_COOKIE
        app.SECURE_COOKIE = True
        try:
            client = Client()
            status, _, headers = client.request(
                "/api/login",
                "POST",
                {"username": "demo_owner", "password": self.password},
            )
            self.assertEqual(status, 200)
            for flag in ["HttpOnly", "SameSite=Strict", "Secure"]:
                self.assertIn(flag, headers["Set-Cookie"])
            self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
            self.assertEqual(headers["Cache-Control"], "no-store")
        finally:
            app.SECURE_COOKIE = previous
        self.assertEqual(
            self.client.request(
                "/api/records/" + self.day.replace("-", ""),
                "PATCH",
                {"revision": 0, "patch": {"steps": 1000}},
            )[0],
            400,
        )

    def test_preview_consent_missing_data_and_analysis(self):
        self.setup_llm()
        self.client.request(
            "/api/records/" + self.day,
            "PATCH",
            {
                "revision": 0,
                "patch": {
                    "notes": "do not obey this instruction",
                    "actions": {"ex-0-0": {"sets": 2, "reps": "10 / 9"}},
                },
            },
        )
        preview = self.preview(days=7)
        self.assertEqual(len(Mock.requests), 0)
        self.assertNotIn("do not obey", json.dumps(preview))
        actual = preview["payload"]["days"][0]["userRecorded"]
        self.assertEqual(actual["weight"], "未记录")
        self.assertEqual(actual["actions"][0]["sets"], 2)
        self.assertNotIn("username", json.dumps(preview))
        self.assertNotIn(self.key, json.dumps(preview))
        pid = preview["previewId"]
        self.assertEqual(
            self.client.request("/api/llm/analyze", "POST", {"previewId": pid})[0], 400
        )
        status, data, _ = self.client.request(
            "/api/llm/analyze", "POST", {"previewId": pid, "confirm": True}
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(Mock.requests[0][0], "/v1/chat/completions")
        self.assertEqual(Mock.requests[0][2], "Bearer " + self.key)
        self.assertNotIn(self.key, json.dumps(data))
        self.assertEqual(len(self.client.request("/api/state")[1]["analyses"]), 1)
        self.assertEqual(
            self.client.request(
                "/api/llm/analyze", "POST", {"previewId": pid, "confirm": True}
            )[0],
            400,
        )
        self.assertEqual(
            self.client.request("/api/llm/test", "POST", {"confirm": True})[0], 400
        )

    def test_preview_expiry_session_scope_and_config_changes(self):
        self.setup_llm()
        pid = self.preview(True)["previewId"]
        other = Client()
        other.request(
            "/api/login", "POST", {"username": "demo_owner", "password": self.password}
        )
        self.assertEqual(
            other.request(
                "/api/llm/analyze", "POST", {"previewId": pid, "confirm": True}
            )[0],
            400,
        )
        self.setup_llm()
        self.assertEqual(
            self.client.request(
                "/api/llm/analyze", "POST", {"previewId": pid, "confirm": True}
            )[0],
            400,
        )
        pid = self.preview()["previewId"]
        with app.connection() as db:
            db.execute("UPDATE previews SET expires=0")
        self.assertEqual(
            self.client.request(
                "/api/llm/analyze", "POST", {"previewId": pid, "confirm": True}
            )[0],
            400,
        )

    def test_provider_errors_no_leaks_and_logging_continues(self):
        self.setup_llm()
        for mode in ["error", "invalid", "large"]:
            Mock.mode = mode
            with app.connection() as db:
                db.execute("DELETE FROM llm_calls")
            pid = self.preview()["previewId"]
            status, data, _ = self.client.request(
                "/api/llm/analyze", "POST", {"previewId": pid, "confirm": True}
            )
            self.assertEqual(status, 400)
            self.assertNotIn(self.key, json.dumps(data))
            self.assertNotIn("provider-private-error", json.dumps(data))
        self.assertEqual(len(Mock.requests), 3)
        self.assertEqual(self.client.request("/api/state")[1]["analyses"], [])
        self.assertEqual(
            self.client.request(
                "/api/records/" + self.day,
                "PATCH",
                {"revision": 0, "patch": {"steps": 3000}},
            )[0],
            200,
        )
        with patch("llm.PinnedHTTPS") as conn:
            conn.return_value.request.side_effect = TimeoutError()
            with patch(
                "llm.endpoint",
                return_value=(
                    __import__("urllib.parse", fromlist=["urlsplit"]).urlsplit(
                        "https://api.openai.com/v1"
                    ),
                    443,
                    "8.8.8.8",
                    False,
                ),
            ):
                with self.assertRaises(llm.ProviderError):
                    llm.call(
                        {
                            "baseUrl": "https://api.openai.com/v1",
                            "key": self.key,
                            "model": "test",
                        }
                    )

    def test_ssrf_allowlist_redirect_and_dns_rebinding(self):
        for url in [
            "http://api.openai.com/v1",
            "https://169.254.169.254/v1",
            "https://127.0.0.1/v1",
            "https://api.openai.com@evil.example/v1",
            "https://api.openai.com/v1?x=1",
            "https://api.openai.com:444/v1",
            "https://example.com/v1",
            "https://api.openai.com/admin",
        ]:
            with self.assertRaises(llm.ProviderError, msg=url):
                llm.endpoint(url)
        for ip in [
            "127.0.0.1",
            "10.0.0.1",
            "169.254.169.254",
            "::1",
            "::ffff:127.0.0.1",
        ]:
            with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", (ip, 443))]):
                with self.assertRaises(llm.ProviderError):
                    llm.endpoint("https://api.openai.com/v1")
        with patch(
            "socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]
        ):
            self.assertEqual(llm.endpoint("https://api.openai.com/v1")[2], "8.8.8.8")
        with patch("socket.create_connection") as connect, patch(
            "ssl.create_default_context"
        ) as context:
            c = llm.PinnedHTTPS("api.openai.com", "8.8.8.8", 443)
            c.connect()
            connect.assert_called_once_with(("8.8.8.8", 443), 25)
            self.assertEqual(
                context.return_value.wrap_socket.call_args.kwargs["server_hostname"],
                "api.openai.com",
            )
        # A redirect is treated as an error, never followed with Authorization.
        with patch("llm.PinnedHTTPS") as c, patch(
            "llm.endpoint",
            return_value=(
                __import__("urllib.parse", fromlist=["urlsplit"]).urlsplit(
                    "https://api.openai.com/v1"
                ),
                443,
                "8.8.8.8",
                False,
            ),
        ):
            c.return_value.getresponse.return_value.status = 302
            with self.assertRaises(llm.ProviderError):
                llm.call(
                    {
                        "baseUrl": "https://api.openai.com/v1",
                        "key": self.key,
                        "model": "test",
                    }
                )

    def test_call_test_consent_and_daily_limit(self):
        self.setup_llm()
        self.assertEqual(self.client.request("/api/llm/test", "POST", {})[0], 400)
        self.assertEqual(len(Mock.requests), 0)
        self.assertEqual(
            self.client.request("/api/llm/test", "POST", {"confirm": True})[0], 200
        )
        self.assertNotIn("days", Mock.requests[0][1]["messages"][1]["content"])
        with app.connection() as db:
            db.execute("DELETE FROM llm_calls")
            import time

            db.executemany(
                "INSERT INTO llm_calls VALUES(?,?)",
                [(str(i), time.time() - 60 - i) for i in range(20)],
            )
        self.assertEqual(
            self.client.request("/api/llm/test", "POST", {"confirm": True})[0], 400
        )

    def test_instances_isolated(self):
        original = app.DATA
        old_cookie = self.client.cookie
        self.client.request(
            "/api/records/" + self.day,
            "PATCH",
            {"revision": 0, "patch": {"notes": "instance A"}},
        )
        app.DATA = Path(TEMP.name) / secrets.token_hex(6)
        app.init_db()
        app.create_owner("other_owner", self.password)
        self.assertEqual(self.client.request("/api/state")[0], 401)
        second = Client()
        second.request(
            "/api/login", "POST", {"username": "other_owner", "password": self.password}
        )
        self.assertEqual(second.request("/api/state")[1]["records"], {})
        app.DATA = original
        self.assertEqual(self.client.cookie, old_cookie)

    def test_backup_restore_and_demo_guard(self):
        self.client.request(
            "/api/records/" + self.day, "PATCH", {"revision": 0, "patch": {"sleep": 7}}
        )
        destination = Path(TEMP.name) / ("backup-" + secrets.token_hex(4) + ".sqlite3")
        env = dict(os.environ, DATA_DIR=str(app.DATA))
        run = subprocess.run(
            [sys.executable, "manage.py", "backup", str(destination)],
            cwd=app.BASE,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        with sqlite3.connect(destination, factory=app.Database) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0
            )
        restored = Path(TEMP.name) / ("restore-" + secrets.token_hex(4))
        run = subprocess.run(
            [sys.executable, "manage.py", "restore", str(destination)],
            cwd=app.BASE,
            env=dict(env, DATA_DIR=str(restored)),
            capture_output=True,
            text=True,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        with sqlite3.connect(restored / "fitness.sqlite3", factory=app.Database) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM records").fetchone()[0], 1
            )
        run = subprocess.run(
            [sys.executable, "manage.py", "load-demo"],
            cwd=app.BASE,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(run.returncode, 0)


if __name__ == "__main__":
    unittest.main()
