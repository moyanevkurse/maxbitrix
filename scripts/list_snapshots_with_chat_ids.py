#!/usr/bin/env python3
"""
Print contacts/companies snapshots that have non-empty chat IDs.

Usage:
  python scripts/list_snapshots_with_chat_ids.py
  python scripts/list_snapshots_with_chat_ids.py --kind contacts
  python scripts/list_snapshots_with_chat_ids.py --kind companies
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


COMPANY_ID_KEYS = (
    "UF_CRM_CHATAPP_DIALOG",
    "UF_CRM_1774872874",
    "UF_CRM_1774872975",
    "UF_CRM_1774873038",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="List snapshots with non-empty chat IDs")
    parser.add_argument(
        "--kind",
        choices=("all", "contacts", "companies"),
        default="all",
        help="What to print (default: all)",
    )
    return parser.parse_args()


def setup_django() -> None:
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()


def print_contacts() -> int:
    from mailings.models import BitrixContactSnapshot

    count = 0
    print("\n=== Contacts with IDs ===")
    qs = BitrixContactSnapshot.objects.all().only(
        "bitrix_contact_id",
        "full_name",
        "max_chat_id",
        "telegram_id",
    )
    for item in qs:
        fields = []
        if (item.max_chat_id or "").strip():
            fields.append(f"max_chat_id={item.max_chat_id.strip()}")
        if (item.telegram_id or "").strip():
            fields.append(f"telegram_id={item.telegram_id.strip()}")
        if not fields:
            continue
        count += 1
        print(f"contact_id={item.bitrix_contact_id} name={item.full_name or '-'} | " + ", ".join(fields))
    print(f"Total contacts: {count}")
    return count


def print_companies() -> int:
    from mailings.models import BitrixCompanySnapshot

    count = 0
    print("\n=== Companies with IDs ===")
    qs = BitrixCompanySnapshot.objects.all().only("bitrix_company_id", "title", "raw_data")
    for item in qs:
        raw = item.raw_data or {}
        fields = []
        for key in COMPANY_ID_KEYS:
            value = str(raw.get(key) or "").strip()
            if value:
                fields.append(f"{key}={value}")
        if not fields:
            continue
        count += 1
        print(f"company_id={item.bitrix_company_id} title={item.title or '-'} | " + ", ".join(fields))
    print(f"Total companies: {count}")
    return count


def main() -> int:
    args = parse_args()
    setup_django()
    contacts = companies = 0
    if args.kind in ("all", "contacts"):
        contacts = print_contacts()
    if args.kind in ("all", "companies"):
        companies = print_companies()
    print(f"\nDone: contacts={contacts}, companies={companies}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
