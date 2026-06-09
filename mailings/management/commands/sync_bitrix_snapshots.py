from django.core.management.base import BaseCommand

from mailings.services.bitrix_sync import sync_bitrix_snapshots


class Command(BaseCommand):
    help = "Sync Bitrix contacts/companies into local snapshot models"

    def add_arguments(self, parser):
        parser.add_argument(
            "--date-modify-from",
            dest="date_modify_from",
            default=None,
            help="ISO datetime filter for incremental sync (example: 2026-04-01T00:00:00+03:00)",
        )

    def handle(self, *args, **options):
        stats = sync_bitrix_snapshots(date_modify_from=options.get("date_modify_from"))
        self.stdout.write(
            self.style.SUCCESS(
                "Bitrix sync done: "
                f"contacts={stats.contacts_saved}/{stats.contacts_total}, "
                f"companies={stats.companies_saved}/{stats.companies_total}"
            )
        )
