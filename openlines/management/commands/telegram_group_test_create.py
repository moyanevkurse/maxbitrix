from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


from openlines.services_telegram_groups import (
    TelegramGroupCreateError,
    create_telegram_group,
)


class Command(BaseCommand):
    help = "Создаёт одну тестовую Telegram-группу без Bitrix. Требует явный --confirm."


    def add_arguments(self, parser):
        parser.add_argument("--title", default="OpTem: тестовая группа")
        parser.add_argument(
            "--member",
            action="append",
            dest="members",
            default=[],
            help="Необязательный @username/номер/TG ID участника; флаг можно указать несколько раз",
        )
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="подтверждает фактическое создание группы в Telegram",
        )


    def handle(self, *args, **options):
        if not options["confirm"]:
            raise CommandError('Команда создаёт реальную группу. Повторите с флагом --confirm.')


        bot_username = str(getattr(settings, "TELEGRAM_GROUP_BOT_USERNAME", "") or "").strip()
        if not bot_username:
            raise CommandError("Укажите TELEGRAM_GROUP_BOT_USERNAME в .env, например @optem_test_bot")


        try:
            result = create_telegram_group(
                title=options["title"],
                member_refs=options["members"] or [],
                bot_username=bot_username,
            )
        except TelegramGroupCreateError as exc:
            raise CommandError(f"Тестовая группа не создана: {exc}") from exc

        self.stdout.write(self.style.SUCCESS(f"Группа создана: chat_id={result['chat_id']}"))
        self.stdout.write(f"Добавлены: {', '.join(result['invited']) or 'никто'}")
        self.stdout.write(f"Права бота: {result['bot_admin']}")
        if result["invite_errors"]:
            self.stderr.write(
                self.style.WARNING(f"Ошибки приглашений: {result['invite_errors']}")
            )
