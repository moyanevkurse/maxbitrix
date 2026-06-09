#!/usr/bin/env python3
"""
One-shot Bitrix sync into local Django DB.

Usage:
  python scripts/sync_bitrix_snapshots.py
  python scripts/sync_bitrix_snapshots.py --date-modify-from "2026-04-01T00:00:00+03:00"
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync Bitrix contacts and companies into snapshot models")
    parser.add_argument(
        "--date-modify-from",
        default=None,
        help="ISO datetime for incremental sync (example: 2026-04-01T00:00:00+03:00)",
    )
    return parser.parse_args()


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

    import django

    django.setup()

    from mailings.services.bitrix_sync import sync_bitrix_snapshots

    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    print("Starting Bitrix sync...", flush=True)
    stats = sync_bitrix_snapshots(date_modify_from=args.date_modify_from)
    print(
        "Bitrix sync done: "
        f"contacts={stats.contacts_saved}/{stats.contacts_total}, "
        f"companies={stats.companies_saved}/{stats.companies_total}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
