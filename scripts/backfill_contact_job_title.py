#!/usr/bin/env python3
"""
Backfill Bitrix contact job title from raw_data field POST.
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

    from mailings.models import BitrixContactSnapshot

    total = 0
    updated = 0
    for item in BitrixContactSnapshot.objects.all().only("id", "raw_data", "job_title"):
        total += 1
        raw = item.raw_data or {}
        new_value = str(raw.get("POST") or "").strip()
        if new_value != (item.job_title or ""):
            item.job_title = new_value
            item.save(update_fields=["job_title"])
            updated += 1

    print(f"Backfill done: updated={updated}, total={total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
