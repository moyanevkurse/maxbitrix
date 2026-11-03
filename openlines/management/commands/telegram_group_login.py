from __future__ import annotations

import os

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Однократно авторизует служебный Telegram-аккаунт для создания групп (без браузера).'

    def handle(self, *args, **options):
        try:
            from openlines.services_telegram_groups import TelegramGroupCreateError, _client, _session_path
        except ImportError as exc:
            raise CommandError('Telethon is not installed. Run pip install -r requirements.txt') from exc

        try:
            client = _client()
        except TelegramGroupCreateError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write('Введите номер, код Telegram и 2FA-пароль в консоли. Браузер не откроется.')
        try:
            client.start()
            if not client.is_user_authorized():
                raise CommandError('Telegram service account was not authorized')
            session_path = _session_path()
            try:
                os.chmod(session_path, 0o600)
            except OSError:
                pass
            self.stdout.write(self.style.SUCCESS(f'Сессия сохранена: {session_path}'))
        finally:
            client.disconnect()

