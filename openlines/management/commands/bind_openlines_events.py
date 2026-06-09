import sys

from django.core.management.base import BaseCommand

from openlines.services import BitrixAuthError, call_bitrix_rest


class Command(BaseCommand):
    help = 'Подписать приложение на события OpenLines (event.bind), чтобы ответы оператора уходили в Telegram.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--handler-url',
            dest='handler_url',
            help='URL обработчика событий (должен быть доступен из интернета). '
                 'По умолчанию берётся из BITRIX_OPENLINE_EVENTS_HANDLER.',
        )
        parser.add_argument(
            '--event',
            dest='event',
            default='OnImConnectorMessageAdd',
            help='Имя события для подписки (по умолчанию OnImConnectorMessageAdd).',
        )

    def handle(self, *args, **options):
        from django.conf import settings

        handler_url = options.get('handler_url') or getattr(settings, 'BITRIX_OPENLINE_EVENTS_HANDLER', '').strip()
        if not handler_url:
            self.stderr.write(
                self.style.ERROR(
                    'Не задан URL обработчика событий. Передайте --handler-url или установите BITRIX_OPENLINE_EVENTS_HANDLER в .env.'
                )
            )
            sys.exit(1)

        event_name = options['event']
        self.stdout.write(f'Подписываемся на событие {event_name} → {handler_url}')
        try:
            result = call_bitrix_rest('event.bind', {'event': event_name, 'handler': handler_url})
        except BitrixAuthError as e:
            self.stderr.write(self.style.ERROR(f'Ошибка Bitrix: {e}'))
            sys.exit(1)
        self.stdout.write(self.style.SUCCESS('event.bind result:'))
        self.stdout.write(str(result))

