#!/usr/bin/env python3
"""
Resolve MAX user_id by phone using WebSocket API (opcode=46).

Example:
  python scripts/max_ws_resolve_phone.py --phone "+79991234567" --token-file profile/max_ws_token.env
"""

from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from websocket import create_connection

LOCAL_CONFIG = {
    "MAX_WS_URL": "wss://ws-api.oneme.ru/websocket",
    "MAX_WS_TOKEN": "",
    "MAX_WS_ORIGIN": "https://web.max.ru",
    "MAX_WS_USER_AGENT": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
    ),
    "MAX_WS_COOKIE": "",
}
DEFAULT_TOKEN_FILE = "profile/max_ws_token.env"


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or LOCAL_CONFIG.get(name) or default or "").strip()


def _read_env_key_from_file(file_path: Path, key: str) -> str:
    if not file_path.exists():
        return ""
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError:
        return ""

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() != key:
            continue
        value = v.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        return value.strip()
    return ""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resolve MAX user_id by phone via WebSocket")
    parser.add_argument("--phone", required=True, help="Phone in international format, e.g. +79991234567")
    parser.add_argument("--ws-url", default="", help="WebSocket URL")
    parser.add_argument("--token", default="", help="MAX WS token")
    parser.add_argument(
        "--token-file",
        default=DEFAULT_TOKEN_FILE,
        help=f"Path to env-like token file with MAX_WS_TOKEN (default: {DEFAULT_TOKEN_FILE})",
    )
    parser.add_argument("--device-id", default="", help="Device UUID (default: random uuid4)")
    parser.add_argument("--origin", default="", help="Origin header")
    parser.add_argument("--user-agent", default="", help="User-Agent header")
    parser.add_argument("--cookie", default="", help="Cookie header")
    parser.add_argument("--timeout", type=int, default=20, help="Socket timeout seconds")
    return parser.parse_args()


def _recv_json(ws, timeout_s: int) -> dict[str, Any]:
    ws.settimeout(timeout_s)
    raw = ws.recv()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    return json.loads(raw)


def _recv_until(ws, timeout_s: int, *, seq: int, opcode: int) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        left = max(1, int(deadline - time.time()))
        msg = _recv_json(ws, left)
        if msg.get("cmd") == 1 and msg.get("seq") == seq and msg.get("opcode") == opcode:
            return msg
    raise TimeoutError(f"Timeout waiting response: seq={seq} opcode={opcode}")


def main() -> None:
    args = _parse_args()
    cwd = Path.cwd()

    ws_url = (args.ws_url or "").strip() or _env("MAX_WS_URL", "wss://ws-api.oneme.ru/websocket")
    token_file = Path(args.token_file)
    if not token_file.is_absolute():
        token_file = (cwd / token_file).resolve()
    token_from_file = _read_env_key_from_file(token_file, "MAX_WS_TOKEN")
    token = (args.token or "").strip() or token_from_file or _env("MAX_WS_TOKEN")
    origin = (args.origin or "").strip() or _env("MAX_WS_ORIGIN", "https://web.max.ru")
    user_agent = (args.user_agent or "").strip() or _env("MAX_WS_USER_AGENT")
    cookie = (args.cookie or "").strip() or _env("MAX_WS_COOKIE")
    device_id = (args.device_id or "").strip() or str(uuid.uuid4())
    phone = str(args.phone or "").strip()

    if not token:
        raise SystemExit("Error: token is required")
    if not phone:
        raise SystemExit("Error: phone is required")

    headers = [f"Origin: {origin}", f"User-Agent: {user_agent}"]
    if cookie:
        headers.append(f"Cookie: {cookie}")

    ws = create_connection(ws_url, header=headers, timeout=args.timeout)
    try:
        init_req = {
            "ver": 11,
            "cmd": 0,
            "seq": 0,
            "opcode": 6,
            "payload": {
                "userAgent": {
                    "deviceType": "WEB",
                    "locale": "ru",
                    "deviceLocale": "ru",
                    "osVersion": "Windows",
                    "deviceName": "Chrome",
                    "headerUserAgent": user_agent,
                    "appVersion": "26.4.1",
                    "screen": "864x1536 1.3x",
                    "timezone": "Asia/Bangkok",
                },
                "deviceId": device_id,
            },
        }
        ws.send(json.dumps(init_req, ensure_ascii=False))
        _recv_until(ws, args.timeout, seq=0, opcode=6)

        auth_seq = 1
        auth_req = {
            "ver": 11,
            "cmd": 0,
            "seq": auth_seq,
            "opcode": 19,
            "payload": {
                "token": token,
                "chatsCount": 40,
                "interactive": True,
                "chatsSync": 0,
                "contactsSync": 0,
                "presenceSync": -1,
                "draftsSync": 0,
            },
        }
        ws.send(json.dumps(auth_req, ensure_ascii=False))
        _recv_until(ws, args.timeout, seq=auth_seq, opcode=19)

        resolve_seq = 2
        resolve_req = {
            "ver": 11,
            "cmd": 0,
            "seq": resolve_seq,
            "opcode": 46,
            "payload": {
                "phone": phone,
            },
        }
        ws.send(json.dumps(resolve_req, ensure_ascii=False))
        resolve_resp = _recv_until(ws, args.timeout, seq=resolve_seq, opcode=46)
        contact = (resolve_resp.get("payload") or {}).get("contact") or {}
        user_id = int(contact.get("id") or 0)
        if user_id <= 0:
            raise RuntimeError(f"Resolve response has no contact.id: {resolve_resp}")
        print(f"Resolve phone OK: user_id={user_id}")
        print(json.dumps(resolve_resp, ensure_ascii=False, indent=2))
    finally:
        ws.close()


if __name__ == "__main__":
    main()
