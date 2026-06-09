#!/usr/bin/env python3
"""
Backfill Bitrix company address from raw_data field UF_CRM_1747678283.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

    import django

    django.setup()

    from mailings.models import BitrixCompanySnapshot

    total = 0
    updated = 0
    for item in BitrixCompanySnapshot.objects.all().only("id", "raw_data", "address"):
        total += 1
        raw = item.raw_data or {}
        new_value = str(raw.get("UF_CRM_1747678283") or "").strip()
        if new_value != (item.address or ""):
            item.address = new_value
            item.save(update_fields=["address"])
            updated += 1

    print(f"Backfill done: updated={updated}, total={total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
