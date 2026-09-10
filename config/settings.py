import os
from pathlib import Path
import environ

env = environ.Env(DEBUG=(bool, False))

BASE_DIR = Path(__file__).resolve().parent.parent
environ.Env.read_env(BASE_DIR / '.env')

SECRET_KEY = env('SECRET_KEY', default='change-me-in-production')
DEBUG = env('DEBUG')
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['localhost', '127.0.0.1'])
if DEBUG:
    ALLOWED_HOSTS += ['.ngrok-free.app', '.ngrok.io']

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'openlines',
    'mailings',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.locale.LocaleMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]
ROOT_URLCONF = 'config.urls'
WSGI_APPLICATION = 'config.wsgi.application'

DB_BACKEND = env('DB_BACKEND', default='sqlite').strip().lower()
DB_CONN_MAX_AGE = env.int('DB_CONN_MAX_AGE', default=60)
DB_SQLITE_TIMEOUT_SEC = env.int('DB_SQLITE_TIMEOUT_SEC', default=20)
DATABASE_URL = env('DATABASE_URL', default='').strip()

if DATABASE_URL:
    DATABASES = {
        'default': env.db('DATABASE_URL'),
    }
elif DB_BACKEND == 'postgres':
    postgres_sslmode = env('POSTGRES_SSLMODE', default='prefer').strip()
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': env('POSTGRES_DB', default='telegram_bot'),
            'USER': env('POSTGRES_USER', default='postgres'),
            'PASSWORD': env('POSTGRES_PASSWORD', default=''),
            'HOST': env('POSTGRES_HOST', default='127.0.0.1'),
            'PORT': env('POSTGRES_PORT', default='5432'),
            'CONN_MAX_AGE': DB_CONN_MAX_AGE,
        }
    }
    if postgres_sslmode:
        DATABASES['default']['OPTIONS'] = {'sslmode': postgres_sslmode}
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': env('SQLITE_NAME', default='db.sqlite3'),
            'OPTIONS': {'timeout': DB_SQLITE_TIMEOUT_SEC},
        }
    }

if 'sqlite' in DATABASES['default'].get('ENGINE', ''):
    name = DATABASES['default'].get('NAME') or 'db.sqlite3'
    DATABASES['default']['NAME'] = str(BASE_DIR / name) if not os.path.isabs(name) else name
    DATABASES['default'].setdefault('OPTIONS', {})
    DATABASES['default']['OPTIONS'].setdefault('timeout', DB_SQLITE_TIMEOUT_SEC)

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'ru'
LANGUAGES = [
    ('ru', 'Русский'),
    ('en', 'English'),
    ('de', 'Deutsch'),
]
LOCALE_PATHS = [BASE_DIR / 'locale']
TIME_ZONE = 'Europe/Moscow'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']

MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {
        "openlines": {"handlers": ["console"], "level": "INFO"},
        "mailings": {"handlers": ["console"], "level": "INFO"},
    },
}


DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Telegram
TELEGRAM_BOT_TOKEN = env('TELEGRAM_BOT_TOKEN', default='')

# Celery
CELERY_BROKER_URL = env('CELERY_BROKER_URL', default='redis://localhost:6379/0')
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE

# Telegram message limits
TELEGRAM_MESSAGE_MAX_LENGTH = 4096
TELEGRAM_CAPTION_MAX_LENGTH = 1024

# Bitrix24 (исходящий вебхук для вызовов API; приём вебхуков — свой URL на нашем бэке)
BITRIX_WEBHOOK_URL = env('BITRIX_WEBHOOK_URL', default='')
BITRIX_REQUEST_TIMEOUT_SEC = env.int('BITRIX_REQUEST_TIMEOUT_SEC', default=60)
# Адрес для snapshot/рассылок: crm.requisite.list + crm.address.list (фактический адрес в карточке).
BITRIX_ADDRESS_USE_REQUISITES = env.bool('BITRIX_ADDRESS_USE_REQUISITES', default=True)
# Секрет для запуска рассылки из Bitrix-роботов (webhook /api/bitrix/run-mailing/).
BITRIX_MAILING_WEBHOOK_SECRET = env('BITRIX_MAILING_WEBHOOK_SECRET', default='')
# Приложение Bitrix24 (локальное серверное) для работы через OAuth, а не через исходящий вебхук.
BITRIX_PORTAL_URL = env('BITRIX_PORTAL_URL', default='')
BITRIX_APP_CLIENT_ID = env('BITRIX_APP_CLIENT_ID', default='local.69a962ec84bea1.12635714')
BITRIX_APP_CLIENT_SECRET = env('BITRIX_APP_CLIENT_SECRET', default='sMWETTGmZT0uPMG8X69Owsur3UWTh3CG7yJHT9kN05vsZhdALW')
# Стадия сделки, при переходе в которую шлём рассылку в Telegram (из контакта: Telegram ID + TG Template)
BITRIX_STAGE_MAILING = env('BITRIX_STAGE_MAILING', default='PREPAYMENT_INVOICE')
# Поле сделки (UF_CRM_...): если true/1/Y — тоже отправлять в TG (не только по стадии)
BITRIX_UF_DEAL_SEND_TG = env('BITRIX_UF_DEAL_SEND_TG', default='UF_CRM_1771569433076')
# Коды полей контакта в Битрикс (UF_CRM_...)
BITRIX_UF_TELEGRAM_ID = env('BITRIX_UF_TELEGRAM_ID', default='UF_CRM_1770617684')
BITRIX_UF_TG_TEMPLATE = env('BITRIX_UF_TG_TEMPLATE', default='UF_CRM_1770624168')
BITRIX_UF_TG_SEND = env('BITRIX_UF_TG_SEND', default='UF_CRM_1770624193')

# OpenLines connector settings (для imconnector.register / imconnector.send.messages)
BITRIX_OPENLINE_CONNECTOR_ID = env('BITRIX_OPENLINE_CONNECTOR_ID', default='')
BITRIX_OPENLINE_LINE_ID = env('BITRIX_OPENLINE_LINE_ID', default='')
BITRIX_OPENLINE_PLACEMENT_HANDLER = env('BITRIX_OPENLINE_PLACEMENT_HANDLER', default='')
BITRIX_OPENLINE_EVENTS_HANDLER = env('BITRIX_OPENLINE_EVENTS_HANDLER', default='')

# MAX messenger integration
MAX_BOT_TOKEN = env('MAX_BOT_TOKEN', default='')
MAX_API_BASE_URL = env('MAX_API_BASE_URL', default='https://platform-api.max.ru')
MAX_WEBHOOK_SECRET = env('MAX_WEBHOOK_SECRET', default='')

# MAX group creation webhook/task settings
MAX_GROUP_WEBHOOK_SECRET = env('MAX_GROUP_WEBHOOK_SECRET', default='')
MAX_GROUP_PROFILE_DIR = env('MAX_GROUP_PROFILE_DIR', default='profile/.max-manual-profile')
MAX_GROUP_TOKEN_FILE = env('MAX_GROUP_TOKEN_FILE', default='profile/max_ws_token.env')
MAX_GROUP_REFRESH_TIMEOUT = env.int('MAX_GROUP_REFRESH_TIMEOUT', default=45)
MAX_GROUP_REFRESH_EACH_TASK = env.bool('MAX_GROUP_REFRESH_EACH_TASK', default=False)
MAX_GROUP_REFRESH_ON_AUTH_FAIL = env.bool('MAX_GROUP_REFRESH_ON_AUTH_FAIL', default=True)
MAX_GROUP_BOT_USER_ID = env.int('MAX_GROUP_BOT_USER_ID', default=0)
MAX_GROUP_BOT_GRANT_ADMIN = env.bool('MAX_GROUP_BOT_GRANT_ADMIN', default=True)
# Разрешить создание MAX-группы даже без найденных контактов:
# в этом режиме группа создаётся с ботом (MAX_GROUP_BOT_USER_ID) как первым участником.
MAX_GROUP_ALLOW_EMPTY_WITH_BOT = env.bool('MAX_GROUP_ALLOW_EMPTY_WITH_BOT', default=False)
# Минимальный интервал (в секундах) между запросами создания MAX-группы.
# 0 = без ограничения.
MAX_GROUP_MIN_INTERVAL_SEC = env.int('MAX_GROUP_MIN_INTERVAL_SEC', default=0)
# Минимальный интервал между запусками Celery-задачи create_max_group_from_webhook.
# Нужен как защита от дублей при повторных webhook/ретраях.
MAX_GROUP_TASK_MIN_INTERVAL_SEC = env.int('MAX_GROUP_TASK_MIN_INTERVAL_SEC', default=60)
MAX_GROUP_WELCOME_TEXT = env('MAX_GROUP_WELCOME_TEXT', default='')
MAX_GROUP_WELCOME_DELAY_SEC = env.int('MAX_GROUP_WELCOME_DELAY_SEC', default=60)
# Добавить invite-ссылку в конец приветствия (отдельное сообщение со ссылкой тогда не шлём).
MAX_GROUP_WELCOME_INCLUDE_INVITE_LINK = env.bool('MAX_GROUP_WELCOME_INCLUDE_INVITE_LINK', default=True)
MAX_GROUP_WELCOME_INVITE_SUFFIX = env(
    'MAX_GROUP_WELCOME_INVITE_SUFFIX',
    default=' {link}',
)
# Файлы из MEDIA_ROOT: фото «Гибка профиля» + каталоги PDF (разделитель |).
MAX_GROUP_WELCOME_MEDIA_ENABLED = env.bool('MAX_GROUP_WELCOME_MEDIA_ENABLED', default=True)
MAX_GROUP_WELCOME_MEDIA_FILES = env(
    'MAX_GROUP_WELCOME_MEDIA_FILES',
    default=(
        'Гибка профиля.png|'
        'Decoplume каталог 2026.pdf|'
        'LACONISTIQ & DECOPLUME v3.4 (2026).pdf|'
        'Laconistiq каталог 2026.pdf'
    ),
)
MAX_GROUP_WELCOME_MEDIA_DELAY_SEC = env.float('MAX_GROUP_WELCOME_MEDIA_DELAY_SEC', default=1.5)
MAX_GROUP_INVITE_LINK_ENABLED = env.bool('MAX_GROUP_INVITE_LINK_ENABLED', default=True)
MAX_GROUP_INVITE_LINK_DELAY_SEC = env.int('MAX_GROUP_INVITE_LINK_DELAY_SEC', default=5)
MAX_GROUP_INVITE_LINK_TEXT = env(
    'MAX_GROUP_INVITE_LINK_TEXT',
    default='Ссылка для входа в группу:\n{link}',
)
# Пауза перед добавлением участников в только что созданный чат (секунды).
MAX_GROUP_ADD_MEMBERS_DELAY_SEC = env.int('MAX_GROUP_ADD_MEMBERS_DELAY_SEC', default=15)
# Разрешить fallback-резолв MAX user_id по телефону через WS (opcode=46), если обычный резолв user_ids пустой.
MAX_GROUP_PHONE_RESOLVE_ENABLED = env.bool('MAX_GROUP_PHONE_RESOLVE_ENABLED', default=True)
# Таймаут WS-резолва user_id по телефону.
MAX_GROUP_PHONE_RESOLVE_TIMEOUT_SEC = env.int('MAX_GROUP_PHONE_RESOLVE_TIMEOUT_SEC', default=20)
# Пауза между WS-запросами phone->user_id (рандом в диапазоне min..max, секунды).
MAX_GROUP_PHONE_RESOLVE_DELAY_MIN_SEC = env.int('MAX_GROUP_PHONE_RESOLVE_DELAY_MIN_SEC', default=10)
MAX_GROUP_PHONE_RESOLVE_DELAY_MAX_SEC = env.int('MAX_GROUP_PHONE_RESOLVE_DELAY_MAX_SEC', default=20)
# Список user_id MAX, которых нельзя добавлять в создаваемые группы (CSV).
MAX_GROUP_EXCLUDE_USER_IDS = env('MAX_GROUP_EXCLUDE_USER_IDS', default='')
# При создании группы из контакта: добавить ответственного + сотрудников из админ-справочника.
MAX_GROUP_CONTACT_ADD_COMPANY_MEMBERS = env.bool('MAX_GROUP_CONTACT_ADD_COMPANY_MEMBERS', default=True)
MAX_GROUP_CONTACT_STAFF_ROSTER_LIMIT = env.int('MAX_GROUP_CONTACT_STAFF_ROSTER_LIMIT', default=30)
# Общий лимит сотрудников из админ-справочника (если задан — перекрывает CONTACT_STAFF_ROSTER_LIMIT).
MAX_GROUP_STAFF_ROSTER_LIMIT = env.int('MAX_GROUP_STAFF_ROSTER_LIMIT', default=10)
# Добавлять сотрудников из справочника при создании группы из компании.
MAX_GROUP_COMPANY_ADD_STAFF_ROSTER = env.bool('MAX_GROUP_COMPANY_ADD_STAFF_ROSTER', default=True)
# Лимит сотрудников из справочника только для групп из компании (0 = как у контакта).
MAX_GROUP_COMPANY_STAFF_ROSTER_LIMIT = env.int('MAX_GROUP_COMPANY_STAFF_ROSTER_LIMIT', default=10)
# UF контакта-сотрудника: привязка к user Bitrix и MAX user_id.
BITRIX_UF_EMPLOYEE_BITRIX_USER_ID = env('BITRIX_UF_EMPLOYEE_BITRIX_USER_ID', default='')
BITRIX_UF_EMPLOYEE_MAX_USER_ID = env('BITRIX_UF_EMPLOYEE_MAX_USER_ID', default='')
# UF на user.get (если MAX id хранится на пользователе, а не на контакте-сотруднике).
BITRIX_UF_USER_MAX_USER_ID = env('BITRIX_UF_USER_MAX_USER_ID', default='UF_USR_1779878090247')
# Публичный https URL аватарки, который будет ставиться новой MAX-группе после создания.
MAX_GROUP_AVATAR_URL = env('MAX_GROUP_AVATAR_URL')
# Отправлять ли системное уведомление при смене аватарки группы.
MAX_GROUP_AVATAR_NOTIFY = env.bool('MAX_GROUP_AVATAR_NOTIFY', default=False)
# Связка MAX-группы с контактом Bitrix при bot_added / chat_title_changed
MAX_GROUP_BITRIX_SYNC_ENABLED = env.bool('MAX_GROUP_BITRIX_SYNC_ENABLED', default=True)
MAX_GROUP_BITRIX_SYNC_EVENTS = env('MAX_GROUP_BITRIX_SYNC_EVENTS', default='bot_added,chat_title_changed')
MAX_GROUP_BITRIX_SYNC_TITLE_DELAY_SEC = env.int('MAX_GROUP_BITRIX_SYNC_TITLE_DELAY_SEC', default=3)
BITRIX_UF_MESSENGER_CHAT_NUMBER = env('BITRIX_UF_MESSENGER_CHAT_NUMBER', default='UF_CRM_1742560088')
BITRIX_UF_COMPANY_MESSENGER_CHAT_NUMBER = env(
    'BITRIX_UF_COMPANY_MESSENGER_CHAT_NUMBER',
    default='UF_CRM_1779436763',
)
BITRIX_UF_COMPANY_MAX_CHAT_ID = env(
    'BITRIX_UF_COMPANY_MAX_CHAT_ID',
    default='UF_CRM_1779436775',
)
BITRIX_UF_MAX_CHAT_ID = env('BITRIX_UF_MAX_CHAT_ID', default='UF_CRM_1774872874')
