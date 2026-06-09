#!/usr/bin/env python3
"""
Backfill Bitrix contact last sale datetime from raw_data field UF_CRM_CONTLASTSALE.
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
    from django.utils.dateparse import parse_datetime

    django.setup()

    from mailings.models import BitrixContactSnapshot

    total = 0
    updated = 0
    for item in BitrixContactSnapshot.objects.all().only("id", "raw_data", "last_sale_at"):
        total += 1
        raw = item.raw_data or {}
        parsed = parse_datetime(str(raw.get("UF_CRM_CONTLASTSALE") or "").strip())
        if parsed != item.last_sale_at:
            item.last_sale_at = parsed
            item.save(update_fields=["last_sale_at"])
            updated += 1

    print(f"Backfill done: updated={updated}, total={total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
