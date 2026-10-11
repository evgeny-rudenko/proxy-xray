#!/usr/bin/env python3
"""Compare private operator inputs with tracked source/history without printing secrets."""
from pathlib import Path
import json
import subprocess
from urllib.parse import urlsplit, parse_qs

root = Path(__file__).resolve().parent.parent
needles = set()
env = root / ".env"
if env.exists():
    for line in env.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip("\"'")
        if key.strip() in {"XRAY_SUB_URL", "INBOUND_VLESS_ID", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"} and len(value) >= 8:
            needles.add(value.encode())
extra = root / "vless-extra.txt"
if extra.exists():
    for line in extra.read_text().splitlines():
        if not line.strip().startswith("vless://"):
            continue
        uri = line.strip()
        parsed = urlsplit(uri)
        needles.add(uri.encode())
        if parsed.username:
            needles.add(parsed.username.encode())
        for key, values in parse_qs(parsed.query).items():
            if key in {"privateKey", "password", "token"}:
                needles.update(value.encode() for value in values if len(value) >= 8)

def git(*args):
    return subprocess.check_output(["git", *args], cwd=root)

files = git("ls-files", "--cached", "--others", "--exclude-standard", "-z").split(b"\0")
matches = []
for name in files:
    if not name:
        continue
    path = root / name.decode()
    if path.is_file() and any(value in path.read_bytes() for value in needles):
        matches.append(name.decode())
history = git("log", "--all", "-p", "--format=")
history_matches = sum(value in history for value in needles)
print(json.dumps({"checked_private_values": len(needles), "working_tree_matches": matches,
                  "history_value_matches": history_matches}))
raise SystemExit(bool(matches or history_matches))
