#!/usr/bin/env python3
"""
Генератор секрета для MAX webhook + регистрация подписки в MAX.

Использование:
  python scripts/generate_max_webhook_secret.py
  python scripts/generate_max_webhook_secret.py --length 64
  python scripts/generate_max_webhook_secret.py --register --token <MAX_BOT_TOKEN> --webhook-url https://example.com/bitrix/max/webhook/
"""

import argparse
import os
import secrets
import string
from typing import List

import requests


ALPHABET = string.ascii_letters + string.digits + "_-"
DEFAULT_LENGTH = 48
MIN_LENGTH = 5
MAX_LENGTH = 256

# Локальная конфигурация для удобного запуска без аргументов CLI.
# ВАЖНО: не коммитьте реальные секреты/токены в репозиторий.
LOCAL_CONFIG = {
    "MAX_BOT_TOKEN": "f9LHodD0cOLGsRzg4Q1Fs6BsRXv0zZV2PtUcDj4rkQMujRzThqpCwwQLz5myW7vFNGAEeCXvQ1jwHcQhTcdf",
    "MAX_WEBHOOK_URL": "https://62e6-42-119-200-16.ngrok-free.app/bitrix/max/webhook/",
    "MAX_API_BASE_URL": "https://platform-api.max.ru",
    "MAX_UPDATE_TYPES": "message_created",
    "MAX_WEBHOOK_SECRET": "PFKMJZcTos1ICpPhP9OOMsyz5do10mT-Zbhmnl1BkVf1rZhW",
    "MAX_WEBHOOK_SECRET_LENGTH": "48",
}


def generate_secret(length: int) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate MAX webhook secret and optionally register MAX subscription")
    parser.add_argument(
        "--length",
        type=int,
        default=None,
        help=f"Secret length ({MIN_LENGTH}-{MAX_LENGTH}), default: {DEFAULT_LENGTH}",
    )
    parser.add_argument(
        "--secret",
        type=str,
        default="",
        help="Use this secret instead of generating new one",
    )
    parser.add_argument(
        "--register",
        action="store_true",
        help="Register webhook in MAX via POST /subscriptions",
    )
    parser.add_argument(
        "--token",
        type=str,
        default="",
        help="MAX bot token (Authorization header), required with --register",
    )
    parser.add_argument(
        "--webhook-url",
        type=str,
        default="",
        help="Public webhook URL, required with --register",
    )
    parser.add_argument(
        "--api-base-url",
        type=str,
        default="https://platform-api.max.ru",
        help="MAX API base URL (default: https://platform-api.max.ru)",
    )
    parser.add_argument(
        "--update-types",
        type=str,
        default="message_created",
        help='Comma-separated update types (default: "message_created")',
    )
    return parser.parse_args()


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or LOCAL_CONFIG.get(name) or default or "").strip()


def _parse_update_types(raw: str) -> List[str]:
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


def register_subscription(api_base_url: str, token: str, webhook_url: str, secret: str, update_types: List[str]) -> dict:
    url = f"{api_base_url.rstrip('/')}/subscriptions"
    headers = {
        "Authorization": token.strip(),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "url": webhook_url.strip(),
        "update_types": update_types,
        "secret": secret,
    }
    response = requests.post(url, json=payload, headers=headers, timeout=30)
    data = response.json() if response.content else {}
    if not response.ok:
        raise SystemExit(f"MAX subscriptions error {response.status_code}: {data or response.text}")
    return data


def main() -> None:
    args = parse_args()
    env_length = _env("MAX_WEBHOOK_SECRET_LENGTH")
    length = args.length if args.length else int(env_length) if env_length.isdigit() else DEFAULT_LENGTH
    if length < MIN_LENGTH or length > MAX_LENGTH:
        raise SystemExit(f"Error: --length must be between {MIN_LENGTH} and {MAX_LENGTH}")

    secret = (args.secret or "").strip() or _env("MAX_WEBHOOK_SECRET") or generate_secret(length)
    print(f"MAX_WEBHOOK_SECRET={secret}")

    if not args.register:
        return

    token = (args.token or "").strip() or _env("MAX_BOT_TOKEN")
    webhook_url = (args.webhook_url or "").strip() or _env("MAX_WEBHOOK_URL")
    if not token:
        raise SystemExit("Error: --token is required with --register (or set MAX_BOT_TOKEN)")
    if not webhook_url:
        raise SystemExit("Error: --webhook-url is required with --register (or set MAX_WEBHOOK_URL)")
    if not webhook_url.startswith("https://"):
        raise SystemExit("Error: --webhook-url must start with https://")

    update_types_raw = (args.update_types or "").strip() or _env("MAX_UPDATE_TYPES", "message_created")
    update_types = _parse_update_types(update_types_raw)
    if not update_types:
        raise SystemExit("Error: --update-types cannot be empty")

    result = register_subscription(
        api_base_url=(args.api_base_url or "").strip() or _env("MAX_API_BASE_URL", "https://platform-api.max.ru"),
        token=token,
        webhook_url=webhook_url,
        secret=secret,
        update_types=update_types,
    )
    print("MAX subscription result:")
    print(result)


if __name__ == "__main__":
    main()
