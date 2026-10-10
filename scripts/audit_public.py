"""Conservative public-source scan; flags paths/line numbers, never prints secret text."""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "literal-api-secret": re.compile(
        r"\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16})\b"
    ),
    "user-local-path": re.compile(r"/[U]sers/[^/\s]+/|/[h]ome/[^/\s]+/"),
    "jwt": re.compile(
        r"\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\b"
    ),
}
FORBIDDEN_SUFFIXES = {
    ".sqlite",
    ".sqlite3",
    ".db",
    ".xlsx",
    ".log",
    ".pem",
    ".key",
    ".pyc",
}


def check(name, raw, deny):
    problems = []
    parts = Path(name).parts
    if (
        any(
            p in {"data", "backups", ".venv", "__pycache__", "work", "output"}
            for p in parts
        )
        or (Path(name).name.startswith(".env") and Path(name).name != ".env.example")
        or Path(name).suffix in FORBIDDEN_SUFFIXES
    ):
        problems.append((name, 0, "private/runtime-file"))
    try:
        s = raw.decode("utf-8")
    except UnicodeError:
        return problems + [(name, 0, "unexpected-binary")]
    for label, pattern in PATTERNS.items():
        for m in pattern.finditer(s):
            problems.append((name, s[: m.start()].count("\n") + 1, label))
    for literal in deny:
        if literal and literal in s:
            problems.append((name, 0, "forbidden-literal"))
    return problems


def git(*args):
    return subprocess.check_output(
        ["git", "-C", str(ROOT), *args], stderr=subprocess.DEVNULL
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--deny-literal", action="append", default=[])
    parser.add_argument("--history", action="store_true")
    args = parser.parse_args()
    try:
        # Scan the current publication candidate, including non-ignored untracked files.
        names = (
            git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
            .decode()
            .split("\0")
        )
    except subprocess.CalledProcessError:
        names = [
            str(p.relative_to(ROOT))
            for p in ROOT.rglob("*")
            if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts
        ]
    problems = []
    count = 0
    for name in filter(None, names):
        if (ROOT / name).is_file():
            problems += check(name, (ROOT / name).read_bytes(), args.deny_literal)
            count += 1
    commits = 0
    if args.history:
        for commit in git("rev-list", "--all").decode().splitlines():
            commits += 1
            for name in filter(
                None,
                git("ls-tree", "-r", "--name-only", "-z", commit).decode().split("\0"),
            ):
                problems += check(
                    name, git("show", commit + ":" + name), args.deny_literal
                )
            # Commit identity/message is public metadata too.
            problems += check(
                "commit-metadata",
                git("show", "-s", "--format=fuller", commit),
                args.deny_literal,
            )
    for name, line, label in sorted(set(problems)):
        print(f"{name}:{line}: {label}")
    print(
        f"Checked {count} candidate files and {commits} commits; {len(set(problems))} findings."
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
