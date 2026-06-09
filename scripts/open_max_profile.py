#!/usr/bin/env python3
"""
Open MAX web with a persistent Chrome/Chromium profile.

Purpose:
- open browser with an existing profile to verify login session is preserved;
- do not extract tokens and do not modify .env.

Examples:
  python3 scripts/open_max_profile.py
  python3 scripts/open_max_profile.py --profile-dir profile/.max-manual-profile
  python3 scripts/open_max_profile.py --browser chromium --url https://web.max.ru
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


DEFAULT_PROFILE_DIR = "profile/.max-manual-profile"
DEFAULT_URL = "https://web.max.ru"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Open web.max.ru with an existing persistent browser profile."
    )
    parser.add_argument(
        "--profile-dir",
        default=DEFAULT_PROFILE_DIR,
        help=f'Path to profile root (default: "{DEFAULT_PROFILE_DIR}")',
    )
    parser.add_argument(
        "--profile-directory",
        default="Default",
        help='Chrome profile directory name (default: "Default")',
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"URL to open (default: {DEFAULT_URL})",
    )
    parser.add_argument(
        "--browser",
        default="auto",
        choices=["auto", "google-chrome", "chromium", "chromium-browser"],
        help="Browser binary to run (default: auto)",
    )
    parser.add_argument(
        "--new-window",
        action="store_true",
        help="Open URL in a new browser window.",
    )
    return parser.parse_args()


def resolve_browser(binary_name: str) -> str:
    if binary_name != "auto":
        path = shutil.which(binary_name)
        if not path:
            raise RuntimeError(f'Browser "{binary_name}" is not found in PATH.')
        return path

    for candidate in ("google-chrome", "chromium", "chromium-browser"):
        path = shutil.which(candidate)
        if path:
            return path
    raise RuntimeError(
        "No supported browser found (google-chrome/chromium/chromium-browser)."
    )


def main() -> int:
    args = parse_args()
    cwd = Path.cwd()

    profile_dir = Path(args.profile_dir)
    if not profile_dir.is_absolute():
        profile_dir = cwd / profile_dir
    profile_dir = profile_dir.resolve()

    if not profile_dir.exists():
        print(
            f'Profile directory not found: "{profile_dir}"\n'
            "Create/login profile first, then run this script again.",
            file=sys.stderr,
        )
        return 2

    browser = resolve_browser(args.browser)
    command = [
        browser,
        f"--user-data-dir={profile_dir}",
        f"--profile-directory={args.profile_directory}",
    ]
    if args.new_window:
        command.append("--new-window")
    command.append(args.url)

    print(f"Launching browser: {browser}")
    print(f"Profile dir: {profile_dir}")
    print(f"Profile name: {args.profile_directory}")
    print(f"URL: {args.url}")

    try:
        # Start browser and return immediately. Browser process lives independently.
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to start browser: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
