"""
Запуск бота для приёма обновлений: при добавлении в группу/канал
или при сообщении в личку создаётся запись «Получатель» в админке.

Запуск (в отдельном терминале):
  python manage.py run_telegram_bot
"""
import logging
import sys

from django.core.management.base import BaseCommand
from django.conf import settings

from mailings.bot_updates import run_polling
from mailings.services.telegram import get_bot_token


class Command(BaseCommand):
    help = 'Запуск бота: при добавлении в чат или сообщении в личку создаётся получатель в БД.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--timeout',
            type=int,
            default=30,
            help='Таймаут long polling в секундах (по умолчанию 30)',
        )

    def handle(self, *args, **options):
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s %(levelname)s %(message)s',
            stream=sys.stdout,
        )
        try:
            token = get_bot_token()
        except ValueError as e:
            self.stderr.write(self.style.ERROR(str(e)))
            self.stderr.write('Задайте TELEGRAM_BOT_TOKEN в .env')
            sys.exit(1)
        timeout = options['timeout']
        self.stdout.write(
            self.style.SUCCESS(
                f'Бот запущен. Добавьте бота в группу/канал или напишите в личку — получатели появятся в админке. Timeout={timeout}s'
            )
        )
        run_polling(token, timeout=timeout)
