"""Offline owner setup and consistent SQLite backups; never prints credentials."""

import argparse
import getpass
import json
import os
import sqlite3
from pathlib import Path
import app


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    owner = sub.add_parser("create-owner")
    owner.add_argument("--username", required=True)
    sub.add_parser("set-password")
    backup = sub.add_parser("backup")
    backup.add_argument("destination", type=Path)
    restore = sub.add_parser("restore")
    restore.add_argument("source", type=Path)
    sub.add_parser("load-demo")
    args = parser.parse_args()
    try:
        if args.command in {"create-owner", "set-password"}:
            password = getpass.getpass("密码（至少12字符，不回显）：")
            if password != getpass.getpass("再次输入："):
                raise ValueError("两次密码不一致。")
            if args.command == "create-owner":
                app.create_owner(args.username, password)
            else:
                app.set_password(password)
            print("账户已设置。")
        elif args.command == "backup":
            if args.destination.exists():
                raise ValueError("目标文件已存在，请换一个备份文件名。")
            args.destination.parent.mkdir(parents=True, exist_ok=True)
            with app.connection() as source, sqlite3.connect(
                args.destination, factory=app.Database
            ) as target:
                source.backup(target)
                target.execute("DELETE FROM sessions")
                target.execute("DELETE FROM previews")
                target.execute("DELETE FROM login_attempts")
            os.chmod(args.destination, 0o600)
            print("数据库已备份；不包含模型密钥，请单独安全保管配置。")
        elif args.command == "restore":
            if not args.source.is_file():
                raise ValueError("备份不存在。")
            # Stop the service first. Restore deliberately requires an empty record store.
            with app.connection() as target:
                if (
                    target.execute("SELECT 1 FROM records LIMIT 1").fetchone()
                    or target.execute("SELECT 1 FROM owner LIMIT 1").fetchone()
                ):
                    raise ValueError(
                        "请先停服务并将现有 data 目录移至安全位置，向空目录恢复。"
                    )
                uri = args.source.resolve().as_uri() + "?mode=ro"
                with sqlite3.connect(uri, uri=True, factory=app.Database) as source:
                    if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise ValueError("备份完整性检查失败。")
                    source.backup(target)
                target.execute("DELETE FROM sessions")
                target.execute("DELETE FROM previews")
            print("数据库已恢复；所有旧会话已失效。")
        elif args.command == "load-demo":
            with app.connection() as db:
                p = app.profile(db)
                if db.execute("SELECT 1 FROM records LIMIT 1").fetchone():
                    raise ValueError("演示仅可载入空记录库。")
                demo = json.loads((app.BASE / "examples/demo.json").read_text())
                for row in demo["days"]:
                    from datetime import date, timedelta

                    day = (
                        date.fromisoformat(p["startDate"])
                        + timedelta(days=row["offset"])
                    ).isoformat()
                    db.execute(
                        "INSERT INTO records VALUES(?,?,1,0)",
                        (day, json.dumps(row["data"], ensure_ascii=False)),
                    )
            print("虚构演示已载入（不是实测，不包含账户密码）。")
    except (ValueError, app.HTTPError) as e:
        parser.exit(1, (e.message if isinstance(e, app.HTTPError) else str(e)) + "\n")


if __name__ == "__main__":
    main()
