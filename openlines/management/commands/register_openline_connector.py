import base64
import sys

from django.core.management.base import BaseCommand

from openlines.services import BitrixAuthError, call_bitrix_rest, get_default_portal


class Command(BaseCommand):
    help = (
        'Регистрация собственного коннектора OpenLines (imconnector.register).\n'
        'Использует BITRIX_OPENLINE_CONNECTOR_ID из настроек как ID коннектора.\n'
        'После регистрации этот ID используется как CONNECTOR в imconnector.send.messages.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--connector-id',
            dest='connector_id',
            help='ID коннектора (параметр ID в imconnector.register). По умолчанию берётся из BITRIX_OPENLINE_CONNECTOR_ID.',
        )
        parser.add_argument(
            '--name',
            dest='name',
            default='Telegram Bot Connector',
            help='Человекочитаемое имя коннектора в Битрикс.',
        )
        parser.add_argument(
            '--placement-url',
            dest='placement_handler',
            help='URL PLACEMENT_HANDLER (страница настроек коннектора, доступная из портала). '
                 'По умолчанию берётся из BITRIX_OPENLINE_PLACEMENT_HANDLER, если задан.',
        )

    def handle(self, *args, **options):
        from django.conf import settings

        try:
            portal = get_default_portal()
        except BitrixAuthError as e:
            self.stderr.write(self.style.ERROR(f'Ошибка Bitrix: {e}'))
            self.stderr.write(
                'Убедитесь, что BITRIX_PORTAL_URL / BITRIX_APP_CLIENT_ID / BITRIX_APP_CLIENT_SECRET заданы '
                'и приложение установлено в портале.'
            )
            sys.exit(1)

        connector_id = options.get('connector_id') or getattr(
            settings, 'BITRIX_OPENLINE_CONNECTOR_ID', ''
        ).strip()
        if not connector_id:
            self.stderr.write(
                self.style.ERROR(
                    'Не задан ID коннектора. Передайте --connector-id или установите BITRIX_OPENLINE_CONNECTOR_ID в .env.'
                )
            )
            sys.exit(1)

        name = options['name']
        placement_handler = options.get('placement_handler') or getattr(
            settings, 'BITRIX_OPENLINE_PLACEMENT_HANDLER', ''
        ).strip()
        if not placement_handler:
            self.stderr.write(
                self.style.ERROR(
                    'Не задан PLACEMENT_HANDLER. Передайте --placement-url или установите '
                    'BITRIX_OPENLINE_PLACEMENT_HANDLER в .env (публичный URL страницы настроек коннектора).'
                )
            )
            sys.exit(1)

        # Простая SVG-иконка (круг), отданная как DATA_IMAGE (сырой SVG-текст),
        # чтобы удовлетворить обязательный параметр ICON.DATA_IMAGE.
        svg_icon = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 90 90">'
            '<circle cx="45" cy="45" r="40" fill="#1900ff"/></svg>'
        )

        self.stdout.write(
            f'Портал: {portal.portal_url}\n'
            f'Регистрируем коннектор:\n'
            f'  ID               = {connector_id}\n'
            f'  NAME             = {name}\n'
            f'  PLACEMENT_HANDLER= {placement_handler}\n'
        )

        try:
            result = call_bitrix_rest(
                'imconnector.register',
                {
                    'ID': connector_id,
                    'NAME': name,
                    'ICON': {
                        'COLOR': '#1900ff',
                        'SIZE': '90%',
                        'POSITION': 'center',
                        'DATA_IMAGE': svg_icon,
                    },
                    'PLACEMENT_HANDLER': placement_handler,
                    'DEL_EXTERNAL_MESSAGES': 'Y',
                    'EDIT_INTERNAL_MESSAGES': 'Y',
                    'DEL_INTERNAL_MESSAGES': 'Y',
                    'NEWSLETTER': 'Y',
                    'NEED_SYSTEM_MESSAGES': 'Y',
                    'NEED_SIGNATURE': 'Y',
                    'CHAT_GROUP': 'Y',
                },
            )
        except BitrixAuthError as e:
            self.stderr.write(self.style.ERROR(f'Ошибка авторизации Bitrix: {e}'))
            sys.exit(1)
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'imconnector.register вызов завершился с ошибкой: {e}'))
            sys.exit(1)

        self.stdout.write(self.style.SUCCESS('Ответ Bitrix imconnector.register:'))
        self.stdout.write(str(result))

