#!/usr/bin/env python3
"""
Тестовый скрипт: создание группы в MAX через WebSocket API (reverse-engineered).

Пример:
  python scripts/max_ws_create_group.py --token "An_..." --title "Новая группа" --user-ids 123,456
"""

import argparse
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from websocket import create_connection


# Локальная конфигурация для удобного запуска без CLI.
# Не коммитьте реальные токены.
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
    parser = argparse.ArgumentParser(description="Create MAX chat via WebSocket")
    parser.add_argument("--ws-url", default="", help="WebSocket URL (default: MAX_WS_URL or wss://ws-api.oneme.ru/websocket)")
    parser.add_argument("--token", default="", help="MAX WS token (required; env: MAX_WS_TOKEN)")
    parser.add_argument(
        "--token-file",
        default=DEFAULT_TOKEN_FILE,
        help=f"Path to env-like token file with MAX_WS_TOKEN (default: {DEFAULT_TOKEN_FILE})",
    )
    parser.add_argument("--title", default="Новая группа", help='New chat title (default: "Новая группа")')
    parser.add_argument("--user-ids", default="", help="Comma-separated participant user IDs (example: 123,456)")
    parser.add_argument("--device-id", default="", help="Device UUID (default: random uuid4)")
    parser.add_argument("--origin", default="", help="Origin header (default: MAX_WS_ORIGIN or https://web.max.ru)")
    parser.add_argument("--user-agent", default="", help="User-Agent header")
    parser.add_argument("--cookie", default="", help="Cookie header (optional)")
    parser.add_argument("--timeout", type=int, default=20, help="Socket timeout seconds (default: 20)")
    parser.add_argument(
        "--bot-user-id",
        type=int,
        default=0,
        help="Optional bot user ID to add into created group.",
    )
    parser.add_argument(
        "--bot-admin",
        action="store_true",
        help="Grant ADMIN rights to --bot-user-id after adding bot to group.",
    )
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


def _int_list(raw: str) -> list[int]:
    items = [x.strip() for x in (raw or "").split(",") if x.strip()]
    values: list[int] = []
    for item in items:
        values.append(int(item))
    return values


def _send_chat_members_op(
    ws,
    *,
    seq: int,
    chat_id: int,
    user_ids: list[int],
    operation: str,
    timeout: int,
    show_history: bool | None = None,
    role_type: str | None = None,
    permissions: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chatId": chat_id,
        "userIds": user_ids,
        "operation": operation,
    }
    if show_history is not None:
        payload["showHistory"] = bool(show_history)
    if role_type:
        payload["type"] = role_type
    if permissions is not None:
        payload["permissions"] = int(permissions)

    req = {
        "ver": 11,
        "cmd": 0,
        "seq": seq,
        "opcode": 77,
        "payload": payload,
    }
    ws.send(json.dumps(req, ensure_ascii=False))
    return _recv_until(ws, timeout, seq=seq, opcode=77)


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
    user_ids = _int_list(args.user_ids)

    if not token:
        raise SystemExit(
            "Error: token is required (--token, MAX_WS_TOKEN in token file, or MAX_WS_TOKEN env)"
        )

    headers = [f"Origin: {origin}", f"User-Agent: {user_agent}"]
    if cookie:
        headers.append(f"Cookie: {cookie}")

    print(f"Connect: {ws_url}")
    print(f"Token source: {'--token' if (args.token or '').strip() else (str(token_file) if token_from_file else 'env/local config')}")
    ws = create_connection(ws_url, header=headers, timeout=args.timeout)
    try:
        # Step 1: init (opcode=6)
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
        _ = _recv_until(ws, args.timeout, seq=0, opcode=6)
        print("Init OK")

        # Step 2: auth/session sync (opcode=19)
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
        auth_resp = _recv_until(ws, args.timeout, seq=auth_seq, opcode=19)
        profile = auth_resp.get("payload", {}).get("profile", {}).get("contact", {})
        print(f"Auth OK: user_id={profile.get('id')}")

        # Step 3: create chat (opcode=64, CONTROL/new)
        create_seq = 27
        cid = int(time.time() * 1000)
        create_req = {
            "ver": 11,
            "cmd": 0,
            "seq": create_seq,
            "opcode": 64,
            "payload": {
                "message": {
                    "cid": cid,
                    "attaches": [
                        {
                            "_type": "CONTROL",
                            "event": "new",
                            "chatType": "CHAT",
                            "title": args.title,
                            # Пользователей добавляем отдельным шагом через REST API
                            # (см. openlines.tasks -> mailings.services.max.add_chat_members).
                            "userIds": [],
                        }
                    ],
                },
                "notify": True,
            },
        }
        ws.send(json.dumps(create_req, ensure_ascii=False))
        create_resp = _recv_until(ws, args.timeout, seq=create_seq, opcode=64)
        chat_id = create_resp.get("payload", {}).get("chatId")
        if chat_id is None:
            raise RuntimeError(f"Create chat response has no chatId: {create_resp}")
        print(f"Create chat OK: chat_id={chat_id}")
        print(json.dumps(create_resp, ensure_ascii=False, indent=2))

        next_seq = create_seq + 1

        # Step 4: optionally add bot into group and grant admin rights.
        bot_user_id = int(args.bot_user_id or 0)
        if bot_user_id > 0:
            if bot_user_id in user_ids:
                print(f"Bot already present in initial user_ids: bot_user_id={bot_user_id}")
            else:
                add_bot_resp = _send_chat_members_op(
                    ws,
                    seq=next_seq,
                    chat_id=int(chat_id),
                    user_ids=[bot_user_id],
                    operation="add",
                    show_history=True,
                    timeout=args.timeout,
                )
                print(f"Bot added to chat: bot_user_id={bot_user_id}")
                print("Bot add response:")
                print(json.dumps(add_bot_resp, ensure_ascii=False, indent=2))
                next_seq += 1

            if args.bot_admin:
                bot_admin_resp = _send_chat_members_op(
                    ws,
                    seq=next_seq,
                    chat_id=int(chat_id),
                    user_ids=[bot_user_id],
                    operation="add",
                    role_type="ADMIN",
                    permissions=123,
                    timeout=args.timeout,
                )
                print(f"Bot admin granted: bot_user_id={bot_user_id}")
                print("Bot admin response:")
                print(json.dumps(bot_admin_resp, ensure_ascii=False, indent=2))
    finally:
        ws.close()


if __name__ == "__main__":
    main()
