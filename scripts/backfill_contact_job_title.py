#!/usr/bin/env python3
"""
Backfill Bitrix contact job_title from raw_data UF_CRM_1741120826.
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
    from mailings.services.bitrix_sync import resolve_contact_job_title

    total = 0
    updated = 0
    for item in BitrixContactSnapshot.objects.all().only("id", "raw_data", "job_title"):
        total += 1
        raw = item.raw_data or {}
        if not isinstance(raw, dict):
            continue
        new_value = resolve_contact_job_title(raw)
        if new_value != (item.job_title or ""):
            item.job_title = new_value
            item.save(update_fields=["job_title"])
            updated += 1

    print(f"Backfill done: updated={updated}, total={total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
