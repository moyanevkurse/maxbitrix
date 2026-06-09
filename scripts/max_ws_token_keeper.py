#!/usr/bin/env python3
"""
MAX Web token keeper via Playwright persistent profile.

What it does:
1) Opens web.max.ru in a persistent browser profile.
2) Waits until user logs in (QR flow is supported).
3) Extracts token from __oneme_auth in localStorage/sessionStorage.
4) Optionally writes MAX_WS_TOKEN to .env.

Examples:
  python scripts/max_ws_token_keeper.py --save-env
  python scripts/max_ws_token_keeper.py --profile-dir .max-profile --timeout 600
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


DEFAULT_URL = "https://web.max.ru/"
DEFAULT_PROFILE_DIR = ".max-playwright-profile"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Open MAX web app, keep profile, and pull WS token from __oneme_auth"
    )
    parser.add_argument("--url", default=DEFAULT_URL, help=f"Target URL (default: {DEFAULT_URL})")
    parser.add_argument(
        "--profile-dir",
        default=DEFAULT_PROFILE_DIR,
        help=f"Persistent browser profile directory (default: {DEFAULT_PROFILE_DIR})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=900,
        help="Max wait seconds for login/token (default: 900)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run headless browser mode (not recommended for first login)",
    )
    parser.add_argument(
        "--save-env",
        action="store_true",
        help="Write/update MAX_WS_TOKEN in .env",
    )
    parser.add_argument(
        "--refresh-only",
        action="store_true",
        help="Do not expect interactive login; fail fast if saved session/token is missing",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to .env file (default: .env)",
    )
    return parser.parse_args()


def mask(token: str) -> str:
    if len(token) <= 12:
        return token
    return f"{token[:8]}...{token[-6:]}"


def read_auth_json(page) -> dict | None:
    raw = page.evaluate(
        """() => localStorage.getItem('__oneme_auth') || sessionStorage.getItem('__oneme_auth')"""
    )
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def wait_for_token(page, timeout_sec: int) -> tuple[str, int | None]:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        auth = read_auth_json(page)
        if auth:
            token = str(auth.get("token") or "").strip()
            viewer_id = auth.get("viewerId")
            if token:
                try:
                    viewer_id_int = int(viewer_id) if viewer_id is not None else None
                except (TypeError, ValueError):
                    viewer_id_int = None
                return token, viewer_id_int
        time.sleep(1.0)
    raise TimeoutError(
        "Token not found in __oneme_auth within timeout. "
        "Complete login (QR) and try again."
    )


def upsert_env_key(env_path: Path, key: str, value: str) -> None:
    line = f"{key}={value}"
    if not env_path.exists():
        env_path.write_text(line + "\n", encoding="utf-8")
        return

    text = env_path.read_text(encoding="utf-8")
    pattern = re.compile(rf"^(?:export\s+)?{re.escape(key)}=.*$", flags=re.MULTILINE)
    if pattern.search(text):
        text = pattern.sub(line, text)
        env_path.write_text(text, encoding="utf-8")
        return

    suffix = "" if text.endswith("\n") or not text else "\n"
    env_path.write_text(text + suffix + line + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    cwd = Path.cwd()
    profile_dir = Path(args.profile_dir)
    if not profile_dir.is_absolute():
        profile_dir = cwd / profile_dir
    profile_dir.mkdir(parents=True, exist_ok=True)

    print("Opening MAX web app in persistent browser profile...")
    print(f"Profile dir: {profile_dir}")
    if args.refresh_only:
        print("Refresh-only mode: expecting existing saved session (no QR/login).")
    else:
        print("If this is the first launch, complete QR login in opened browser window.")

    try:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=args.headless,
                viewport={"width": 1366, "height": 900},
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(args.url, wait_until="domcontentloaded")

            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except PlaywrightTimeoutError:
                # MAX UI may keep long-lived connections; ignore.
                pass

            wait_timeout = args.timeout
            if args.refresh_only:
                wait_timeout = min(args.timeout, 45)

            token, viewer_id = wait_for_token(page, wait_timeout)
            print(f"Token received: {mask(token)}")
            if viewer_id is not None:
                print(f"viewerId: {viewer_id}")
            print("Profile is saved and can be reused for future token refresh.")

            if args.save_env:
                env_path = Path(args.env_file)
                if not env_path.is_absolute():
                    env_path = cwd / env_path
                upsert_env_key(env_path, "MAX_WS_TOKEN", token)
                print(f"Updated {env_path} with MAX_WS_TOKEN={mask(token)}")

            context.close()
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        return 130
    except TimeoutError as exc:
        if args.refresh_only:
            print(
                "Error: saved session was not restored (token not found quickly). "
                "Run once without --refresh-only and complete QR login.",
                file=sys.stderr,
            )
            return 2
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
