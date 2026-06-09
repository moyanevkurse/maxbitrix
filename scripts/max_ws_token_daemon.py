#!/usr/bin/env python3
"""
MAX WS token daemon via Playwright persistent browser context.

What it does:
1) Launches one persistent Chromium profile and keeps it running.
2) Reads __oneme_auth token from localStorage/sessionStorage.
3) Periodically writes MAX_WS_TOKEN to env-like file.
4) Does not close/reopen browser between refresh cycles.

Examples:
  python scripts/max_ws_token_daemon.py
  python scripts/max_ws_token_daemon.py --headless --interval 300
  python scripts/max_ws_token_daemon.py --profile-dir profile/.max-manual-profile --env-file profile/max_ws_token.env
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

DEFAULT_URL = "https://web.max.ru/"
DEFAULT_PROFILE_DIR = "profile/.max-manual-profile"
DEFAULT_ENV_FILE = "profile/max_ws_token.env"
DEFAULT_INTERVAL_SEC = 300
DEFAULT_WAIT_TIMEOUT_SEC = 45

SHUTDOWN_REQUESTED = False


def _handle_shutdown(signum, _frame) -> None:
    global SHUTDOWN_REQUESTED
    SHUTDOWN_REQUESTED = True
    print(f"Shutdown signal received: {signum}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Keep one browser session alive and periodically refresh MAX_WS_TOKEN."
    )
    parser.add_argument("--url", default=DEFAULT_URL, help=f"Target URL (default: {DEFAULT_URL})")
    parser.add_argument(
        "--profile-dir",
        default=DEFAULT_PROFILE_DIR,
        help=f"Persistent browser profile directory (default: {DEFAULT_PROFILE_DIR})",
    )
    parser.add_argument(
        "--env-file",
        default=DEFAULT_ENV_FILE,
        help=f"Env-like output file (default: {DEFAULT_ENV_FILE})",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL_SEC,
        help=f"Refresh interval in seconds (default: {DEFAULT_INTERVAL_SEC})",
    )
    parser.add_argument(
        "--wait-timeout",
        type=int,
        default=DEFAULT_WAIT_TIMEOUT_SEC,
        help=f"Per-cycle token wait timeout in seconds (default: {DEFAULT_WAIT_TIMEOUT_SEC})",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run headless browser mode (recommended after first successful login/profile bootstrap).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one refresh cycle and exit.",
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
    raise TimeoutError("Token not found in __oneme_auth within timeout.")


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


def sleep_until_next_cycle(interval_sec: int) -> None:
    for _ in range(max(1, interval_sec)):
        if SHUTDOWN_REQUESTED:
            return
        time.sleep(1)


def run_cycle(page, env_path: Path, wait_timeout: int) -> bool:
    token, viewer_id = wait_for_token(page, wait_timeout)
    upsert_env_key(env_path, "MAX_WS_TOKEN", token)
    print(f"MAX_WS_TOKEN updated: {mask(token)}")
    if viewer_id is not None:
        print(f"viewerId: {viewer_id}")
    return True


def main() -> int:
    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    args = parse_args()
    cwd = Path.cwd()

    profile_dir = Path(args.profile_dir)
    if not profile_dir.is_absolute():
        profile_dir = cwd / profile_dir
    profile_dir.mkdir(parents=True, exist_ok=True)

    env_path = Path(args.env_file)
    if not env_path.is_absolute():
        env_path = cwd / env_path
    env_path.parent.mkdir(parents=True, exist_ok=True)

    interval_sec = max(10, int(args.interval))
    wait_timeout = max(5, int(args.wait_timeout))

    print("Starting MAX WS token daemon...")
    print(f"URL: {args.url}")
    print(f"Profile dir: {profile_dir}")
    print(f"Output env file: {env_path}")
    print(f"Interval: {interval_sec}s")
    print(f"Headless: {bool(args.headless)}")

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
                # MAX may keep long-lived connections; continue.
                pass

            cycle = 0
            while not SHUTDOWN_REQUESTED:
                cycle += 1
                print(f"Refresh cycle #{cycle}")
                try:
                    run_cycle(page=page, env_path=env_path, wait_timeout=wait_timeout)
                except Exception as exc:  # noqa: BLE001
                    print(f"Refresh cycle failed: {exc}", file=sys.stderr)
                if args.once:
                    break
                sleep_until_next_cycle(interval_sec)

            print("Stopping daemon...")
            context.close()
    except Exception as exc:  # noqa: BLE001
        print(f"Fatal daemon error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

