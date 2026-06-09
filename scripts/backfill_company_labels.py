#!/usr/bin/env python3
"""
Backfill local labels for already synced Bitrix companies.

Usage:
  python scripts/backfill_company_labels.py
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
    from mailings.services.bitrix_sync import resolve_potential_label, resolve_segment_label

    total = 0
    updated = 0
    for company in BitrixCompanySnapshot.objects.all().only(
        "id",
        "potential_value_id",
        "potential_value",
        "segment_value_id",
        "segment_value",
    ):
        total += 1
        potential = resolve_potential_label(company.potential_value_id)
        segment = resolve_segment_label(company.segment_value_id)
        if potential != (company.potential_value or "") or segment != (company.segment_value or ""):
            company.potential_value = potential
            company.segment_value = segment
            company.save(update_fields=["potential_value", "segment_value"])
            updated += 1

    print(f"Backfill done: updated={updated}, total={total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
