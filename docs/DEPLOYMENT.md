# Развёртывание и настройка: Telegram/MAX Bot + Bitrix24

Полная инструкция по установке проекта, настройке `.env`, Bitrix24, MAX и админки Django.

---

## Содержание

1. [Что делает проект](#1-что-делает-проект)
2. [Требования](#2-требования)
3. [Установка с нуля](#3-установка-с-нуля)
4. [Публичный URL и HTTPS на сервере](#4-публичный-url-и-https-на-сервере)
5. [Развёртывание на сервере (production)](#5-развёртывание-на-сервере-production)
6. [Все URL эндпоинты](#6-все-url-эндпоинты)
7. [Переменные `.env`](#7-переменные-env)
8. [Настройка Bitrix24](#8-настройка-bitrix24)
9. [Настройка MAX](#9-настройка-max)
10. [Браузер MAX / WS-токен (создание групп)](#10-браузер-max--ws-токен-создание-групп)
11. [Open Lines (открытая линия)](#11-open-lines-открытая-линия)
12. [Создание MAX-групп из CRM](#12-создание-max-групп-из-crm)
13. [Рассылки](#13-рассылки)
14. [Админка Django](#14-админка-django)
15. [Команды manage.py и scripts](#15-команды-managepy-и-scripts)
16. [Смена домена / новый портал Bitrix](#16-смена-домена--новый-портал-bitrix)
17. [Типичные проблемы](#17-типичные-проблемы)

---

## 1. Что делает проект

| Функция | Описание |
|---------|----------|
| **MAX-группы из CRM** | Робот Bitrix вызывает webhook → Celery создаёт группу в MAX, добавляет участников, приветствие, аватар |
| **Связка группы с CRM** | По номеру в названии группы (`1213 / LACONISTIQ / ...`) пишет IMOL и UF в контакт/компанию |
| **Open Lines** | Сообщения из MAX-групп и лички попадают в открытую линию Bitrix; ответы оператора уходят обратно в MAX |
| **Рассылки** | Шаблоны из админки → фильтры по snapshot контактов/компаний → отправка в MAX/Telegram |
| **Snapshot Bitrix** | Локальные копии контактов и компаний для фильтрации рассылок |

---

## 2. Требования

- Python 3.10+
- Redis (для Celery)
- SQLite или PostgreSQL
- Публичный HTTPS-домен — Bitrix и MAX должны достучаться до сервера
- Nginx (или другой reverse proxy) + TLS-сертификат (Let's Encrypt)
- Аккаунт Bitrix24 с CRM и Открытыми линиями
- Бот MAX (токен `MAX_BOT_TO
- Playwright + Chromium на сервере
- Постоянный профиль браузера (`MAX_GROUP_PROFILE_DIR`)
- Фоновый демон обновления WS-токена (`scripts/max_ws_token_daemon.py`)

---

## 3. Установка с нуля

```bash
cd Telegram-bot-for-mailings
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Отредактируй .env (см. раздел 6)

python manage.py migrate
python manage.py createsuperuser

# Playwright (для создания MAX-групп)
playwright install chromium
playwright install-deps   # системные зависимости Chromium на Linux
```

Локальная разработка (без production):

```bash
python manage.py runserver 0.0.0.0:8000
celery -A config worker -l info
```

На сервере см. раздел 5 — там gunicorn, nginx, systemd.

---

## 4. Публичный URL и HTTPS на сервере

Все внешние сервисы обращаются к **одному базовому URL**:

```
BASE=https://bot.example.com
```

В `.env`:

```env
ALLOWED_HOSTS=bot.example.com,localhost,127.0.0.1
DEBUG=0
```

Bitrix и MAX принимают только **публичный HTTPS** без редиректа на логин.

После смены домена обнови URL в `.env`, перезапусти сервисы и перерегистрируй webhook'и (раздел 16).

---

## 5. Развёртывание на сервере (production)

### 5.1 Процессы, которые должны работать постоянно

| Процесс | Назначение |
|---------|------------|
| **gunicorn** (или uwsgi) | Django-приложение |
| **celery worker** | Создание MAX-групп, рассылки, sync |
| **redis** | Брокер Celery |
| **nginx** | HTTPS, прокси на gunicorn, раздача `/media/` |
| **max_ws_token_daemon** | Браузер MAX + обновление WS-токена (раздел 10) |

### 5.2 Gunicorn

```bash
pip install gunicorn

gunicorn config.wsgi:application \
  --bind 127.0.0.1:8000 \
  --workers 2 \
  --timeout 120
```

### 5.3 Nginx (пример)

```nginx
server {
    listen 443 ssl;
    server_name bot.example.com;

    ssl_certificate     /etc/letsencrypt/live/bot.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/bot.example.com/privkey.pem;

    client_max_body_size 50M;

    location /static/ {
        alias /path/to/Telegram-bot-for-mailings/staticfiles/;
    }

    location /media/ {
        alias /path/to/Telegram-bot-for-mailings/media/;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
```

Перед этим: `python manage.py collectstatic`

### 5.4 Systemd (примеры)

Файл `/etc/systemd/system/telegram-bot.service`:

```ini
[Unit]
Description=Telegram/MAX Bot Django
After=network.target

[Service]
User=www-data
WorkingDirectory=/path/to/Telegram-bot-for-mailings
EnvironmentFile=/path/to/Telegram-bot-for-mailings/.env
ExecStart=/path/to/venv/bin/gunicorn config.wsgi:application --bind 127.0.0.1:8000 --workers 2 --timeout 120
Restart=always

[Install]
WantedBy=multi-user.target
```

Файл `/etc/systemd/system/telegram-bot-celery.service`:

```ini
[Unit]
Description=Telegram/MAX Bot Celery
After=network.target redis.service

[Service]
User=www-data
WorkingDirectory=/path/to/Telegram-bot-for-mailings
EnvironmentFile=/path/to/Telegram-bot-for-mailings/.env
ExecStart=/path/to/venv/bin/celery -A config worker -l info
Restart=always

[Install]
WantedBy=multi-user.target
```

Файл `/etc/systemd/system/max-ws-token-daemon.service` — см. раздел 10.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now telegram-bot telegram-bot-celery max-ws-token-daemon
```

---

## 6. Все URL эндпоинты

| Назначение | URL |
|------------|-----|
| Bitrix app handler (OAuth, Open Lines события) | `{BASE}/bitrix/handler/` |
| Bitrix app install | `{BASE}/bitrix/install/` |
| MAX webhook (сообщения, bot_added) | `{BASE}/bitrix/max/webhook/` |
| Создание MAX-группы (робот CRM) | `{BASE}/bitrix/max/create-group/?secret=...` |
| Debug создания группы | `{BASE}/bitrix/max/create-group/debug/` |
| Snapshot webhook (ONCRMCONTACT*, ONCRMCOMPANY*) | `{BASE}/api/bitrix/webhook/` |
| Запуск рассылки из робота | `{BASE}/api/bitrix/run-mailing/?secret=...` |
| Рассылка из карточки контакта | `{BASE}/api/bitrix/contact-mailing/` |
| Админка | `{BASE}/admin/` |
| Медиа (аватар групп) | `{BASE}/media/...` |

---

## 7. Переменные `.env`

Шаблон: `.env.example`. Ниже — сгруппированный справочник.

### 7.1 Django / инфраструктура

```env
SECRET_KEY=...
DEBUG=0
ALLOWED_HOSTS=bot.example.com,localhost,127.0.0.1
CELERY_BROKER_URL=redis://localhost:6379/0
DB_BACKEND=sqlite
# или postgres / DATABASE_URL
```

### 7.2 Bitrix — исходящий вебхук (CRM API)

Для `crm.contact.get`, sync snapshot, создание групп (чтение карточек).

```env
BITRIX_WEBHOOK_URL=https://ПОРТАЛ.bitrix24.ru/rest/1/КЛЮЧ/
BITRIX_REQUEST_TIMEOUT_SEC=60
BITRIX_ADDRESS_USE_REQUISITES=true
```

**Где взять:** Bitrix → Разработчикам → Другое → **Исходящий вебхук** → права: `crm`, `user`, `imopenlines` (по необходимости).

### 7.3 Bitrix — локальное приложение (Open Lines, OAuth)

```env
BITRIX_PORTAL_URL=https://ПОРТАЛ.bitrix24.ru
BITRIX_APP_CLIENT_ID=local.xxxxx
BITRIX_APP_CLIENT_SECRET=xxxxx
```

**Где взять:** Разработчикам → Другое → **Локальное приложение**:

| Поле в Bitrix | Значение |
|---------------|----------|
| Путь обработчика | `{BASE}/bitrix/handler/` |
| Путь установки | `{BASE}/bitrix/install/` |
| Права | `im`, `imopenlines`, `imconnector`, `crm` |

После установки приложения на портал в админке Django → **Openlines → Bitrix portals** должен появиться `access_token`.

### 7.4 Bitrix — Open Lines

```env
BITRIX_OPENLINE_CONNECTOR_ID=my_max_connector
BITRIX_OPENLINE_LINE_ID=2
BITRIX_OPENLINE_PLACEMENT_HANDLER=https://BASE/bitrix/handler/
BITRIX_OPENLINE_EVENTS_HANDLER=https://BASE/bitrix/handler/
```

**Узнать ID линии:**

```
https://ПОРТАЛ/rest/1/КЛЮЧ/imopenlines.config.list.get
```

Поле `ID` в ответе → `BITRIX_OPENLINE_LINE_ID`.

### 7.5 Bitrix — кастомные поля (UF)

При **новом портале** коды полей другие! Смотри в CRM → Настройки полей или REST `crm.contact.fields` / `crm.company.fields` / `user.fields`.

```env
# Контакт: номер чата в мессенджере (для поиска по title группы)
BITRIX_UF_MESSENGER_CHAT_NUMBER=UF_CRM_...

# Контакт: MAX chat id (max:-123...)
BITRIX_UF_MAX_CHAT_ID=UF_CRM_...

# Компания: номер чата в мессенджере
BITRIX_UF_COMPANY_MESSENGER_CHAT_NUMBER=UF_CRM_...

# Компания: MAX chat id
BITRIX_UF_COMPANY_MAX_CHAT_ID=UF_CRM_...

# Telegram-рассылки из CRM (если используешь)
BITRIX_UF_TELEGRAM_ID=UF_CRM_...
BITRIX_UF_TG_TEMPLATE=UF_CRM_...
BITRIX_UF_TG_SEND=UF_CRM_...
BITRIX_UF_DEAL_SEND_TG=UF_CRM_...

# MAX user_id ответственного на карточке user (user.get)
BITRIX_UF_USER_MAX_USER_ID=UF_USR_...

# Опционально: UF на контакте-сотруднике
BITRIX_UF_EMPLOYEE_BITRIX_USER_ID=UF_CRM_...
BITRIX_UF_EMPLOYEE_MAX_USER_ID=UF_CRM_...
```

### 7.6 MAX — бот и webhook

```env
MAX_BOT_TOKEN=...
MAX_API_BASE_URL=https://platform-api.max.ru
MAX_WEBHOOK_SECRET=случайная_строка
MAX_UPDATE_TYPES=message_created,bot_added,chat_title_changed
```

### 7.7 MAX — создание групп

```env
MAX_GROUP_WEBHOOK_SECRET=секрет_для_робота_create-group
MAX_GROUP_BOT_USER_ID=123456789
MAX_GROUP_ALLOW_EMPTY_WITH_BOT=true
MAX_GROUP_BOT_GRANT_ADMIN=true
MAX_GROUP_TASK_MIN_INTERVAL_SEC=60
MAX_GROUP_ADD_MEMBERS_DELAY_SEC=15
MAX_GROUP_PHONE_RESOLVE_ENABLED=true
MAX_GROUP_EXCLUDE_USER_IDS=
MAX_GROUP_AVATAR_URL=https://BASE/media/avatar.jpg
```

WS-профиль (для создания групп):

```env
MAX_GROUP_PROFILE_DIR=profile/.max-manual-profile
MAX_GROUP_TOKEN_FILE=profile/max_ws_token.env
```

### 7.8 MAX — приветствие в новой группе

```env
MAX_GROUP_WELCOME_TEXT=Текст приветствия...
MAX_GROUP_WELCOME_DELAY_SEC=60
MAX_GROUP_WELCOME_MEDIA_ENABLED=true
MAX_GROUP_WELCOME_MEDIA_FILES=file1.png|file2.pdf
MAX_GROUP_WELCOME_INCLUDE_INVITE_LINK=true
```

Текст приветствия можно задать в админке: **Openlines → Настройка приветствия MAX-группы**.

### 7.9 MAX — связка группы с Bitrix

```env
MAX_GROUP_BITRIX_SYNC_ENABLED=true
MAX_GROUP_BITRIX_SYNC_EVENTS=bot_added,chat_title_changed
```

### 7.10 Сотрудники в группах (справочник админки)

```env
MAX_GROUP_CONTACT_ADD_COMPANY_MEMBERS=true
MAX_GROUP_CONTACT_STAFF_ROSTER_LIMIT=3
MAX_GROUP_COMPANY_ADD_STAFF_ROSTER=true
MAX_GROUP_COMPANY_STAFF_ROSTER_LIMIT=0
MAX_GROUP_STAFF_ROSTER_LIMIT=0
```

### 7.11 Рассылки

```env
BITRIX_MAILING_WEBHOOK_SECRET=секрет_для_run-mailing
TELEGRAM_BOT_TOKEN=...   # если шлёшь в Telegram
```

---

## 8. Настройка Bitrix24

### 8.1 Чеклист

- [ ] Исходящий вебхук → `BITRIX_WEBHOOK_URL`
- [ ] Локальное приложение установлено на портал
- [ ] Кастомные поля созданы (номер чата, MAX chat id) на контакте и компании
- [ ] Открытая линия создана, ID в `.env`
- [ ] Коннектор `my_max_connector` зарегистрирован и **подключён к линии в UI**
- [ ] Роботы/БП для создания групп и рассылок (по необходимости)

### 8.2 Какие поля создать в CRM

#### Контакт

| Название (пример) | Тип | Код в `.env` |
|-------------------|-----|--------------|
| Номер чата в мессенджере | Строка/число | `BITRIX_UF_MESSENGER_CHAT_NUMBER` |
| MAX chat id | Строка | `BITRIX_UF_MAX_CHAT_ID` |
| IM (мессенджеры) | Стандартное поле IM | IMOL `max:g:...` пишется автоматически |

#### Компания

| Название (пример) | Тип | Код в `.env` |
|-------------------|-----|--------------|
| Номер чата в мессенджере | Строка/число | `BITRIX_UF_COMPANY_MESSENGER_CHAT_NUMBER` |
| MAX chat id | Строка | `BITRIX_UF_COMPANY_MAX_CHAT_ID` |

#### Пользователь (сотрудник)

| Название | Тип | Код в `.env` |
|----------|-----|--------------|
| MAX user id | Строка | `BITRIX_UF_USER_MAX_USER_ID` (`UF_USR_...`) |

### 8.3 Как узнать код поля (UF)

REST (подставь свой вебхук):

```
.../crm.contact.fields
.../crm.company.fields
.../user.fields
```

Или: CRM → Настройки → Настройки CRM → Поля → открыть поле → в API будет `FIELD_NAME`.

### 8.4 Поля, захардкоженные в коде (при новом портале проверь!)

Эти UF **не** вынесены в `.env` — при переносе на новый портал могут потребовать правки в коде:

- `UF_CRM_CONTLASTSALE`, `UF_CRM_LASTSALE` — дата последней продажи
- `UF_CRM_1742563768`, `UF_CRM_1747679145` — потенциал и сегмент компании
- `UF_CRM_1747678283` и др. — регион/город в названии группы
- ID списков сегментов в админке рассылки: `1163`, `1165`, `1167`, ...

### 8.5 REST: полезные методы

| Метод | Зачем |
|-------|-------|
| `user.get?id=1` | Сотрудник портала (не `crm.user.get`!) |
| `user.fields` | Все UF пользователя |
| `crm.contact.get?id=N` | Карточка контакта |
| `crm.company.get?id=N` | Карточка компании |
| `imopenlines.config.list.get` | Список открытых линий и их ID |

---

## 9. Настройка MAX

### 9.1 Webhook подписка

```bash
# Сгенерировать секрет
python scripts/generate_max_webhook_secret.py

# Зарегистрировать в MAX
python scripts/generate_max_webhook_secret.py --register \
  --token "$MAX_BOT_TOKEN" \
  --webhook-url "https://BASE/bitrix/max/webhook/" \
  --update-types "message_created,bot_added,chat_title_changed"
```

Секрет положи в `.env`:

```env
MAX_WEBHOOK_SECRET=...
```

### 9.2 Бот в группах

Бот должен быть **добавлен в MAX-группу** — иначе не придут `bot_added` / `chat_title_changed` и не сработает связка с CRM.

---

## 10. Браузер MAX / WS-токен (создание групп)

Создание MAX-групп из Bitrix идёт через **WebSocket** (`web.max.ru`), а не через официальный REST. Для этого нужен токен `__oneme_auth` из браузерной сессии.

Celery при создании группы читает токен из файла:

```env
MAX_GROUP_PROFILE_DIR=profile/.max-manual-profile
MAX_GROUP_TOKEN_FILE=profile/max_ws_token.env
```

В `profile/max_ws_token.env` периодически обновляется строка `MAX_WS_TOKEN=...`.

### 10.1 Установка Playwright на сервере

```bash
source venv/bin/activate
pip install playwright
playwright install chromium
playwright install-deps    # Ubuntu/Debian: libgtk, fonts и т.д.
```

### 10.2 Первый вход в MAX (один раз)

Нужен аккаунт MAX, с которого создаются группы. Скрипт открывает `web.max.ru`, ждёт QR-логин, сохраняет профиль и токен.

**На сервере без монитора** — два варианта:

**Вариант A:** первый раз залогиниться на локальной машине, скопировать папку `profile/.max-manual-profile` на сервер.

**Вариант B:** на сервере с виртуальным дисплеем:

```bash
sudo apt install xvfb
xvfb-run -a python scripts/max_ws_token_keeper.py \
  --profile-dir profile/.max-manual-profile \
  --save-env --env-file profile/max_ws_token.env \
  --timeout 900
```

Отсканируй QR в MAX с телефона. После успеха в `profile/max_ws_token.env` появится `MAX_WS_TOKEN`.

Команда (если есть дисплей / SSH с X11):

```bash
python scripts/max_ws_token_keeper.py \
  --profile-dir profile/.max-manual-profile \
  --save-env --env-file profile/max_ws_token.env
```

### 10.3 Демон на сервере (основной режим production)

Держит **один** браузер открытым и обновляет токен каждые N секунд. **Рекомендуется** запускать как отдельный systemd-сервис.

```bash
python scripts/max_ws_token_daemon.py \
  --profile-dir profile/.max-manual-profile \
  --env-file profile/max_ws_token.env \
  --headless \
  --interval 300
```

| Флаг | Описание |
|------|----------|
| `--headless` | Без окна (после первого успешного логина) |
| `--interval 300` | Обновлять токен каждые 5 минут |
| `--once` | Один цикл и выход (для проверки) |

**Systemd** `/etc/systemd/system/max-ws-token-daemon.service`:

```ini
[Unit]
Description=MAX WS token daemon (Playwright)
After=network.target

[Service]
User=www-data
WorkingDirectory=/path/to/Telegram-bot-for-mailings
EnvironmentFile=/path/to/Telegram-bot-for-mailings/.env
ExecStart=/path/to/venv/bin/python scripts/max_ws_token_daemon.py \
  --profile-dir profile/.max-manual-profile \
  --env-file profile/max_ws_token.env \
  --headless --interval 300
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Права: пользователь сервиса (`www-data`) должен иметь запись в `profile/`.

### 10.4 Открыть профиль вручную (проверка сессии)

Без Playwright — обычный Chrome/Chromium с сохранённым профилем:

```bash
python scripts/open_max_profile.py \
  --profile-dir profile/.max-manual-profile \
  --url https://web.max.ru
```

Полезно, если нужно перелогиниться или проверить, что сессия жива.

### 10.5 Как это связано с Celery

1. Робот Bitrix → `POST /bitrix/max/create-group/`
2. Celery `create_max_group_from_webhook`
3. При необходимости `refresh_max_ws_token` → вызывает `max_ws_token_keeper.py --refresh-only`
4. `max_ws_create_group.py` — создание чата по WS
5. REST MAX API — добавление участников, приветствие, аватар

Если демон **не запущен** и токен протух — создание группы упадёт с auth-ошибкой.

Настройки в `.env`:

```env
MAX_GROUP_REFRESH_ON_AUTH_FAIL=true
MAX_GROUP_REFRESH_EACH_TASK=false
MAX_GROUP_REFRESH_TIMEOUT=45
```

`REFRESH_EACH_TASK=false` — нормально, если работает `max_ws_token_daemon`.

### 10.6 Вспомогательные скрипты

| Скрипт | Назначение |
|--------|------------|
| `scripts/max_ws_token_keeper.py` | Разовый логин / refresh токена |
| `scripts/max_ws_token_daemon.py` | Постоянный демон (production) |
| `scripts/open_max_profile.py` | Открыть Chrome с профилем вручную |
| `scripts/max_ws_create_group.py` | Создание группы (вызывается из Celery) |
| `scripts/max_ws_resolve_phone.py` | phone → MAX user_id (вызывается из Celery) |

---

## 11. Open Lines (открытая линия)

### 11.1 Команды (один раз на портал / после смены URL)

```bash
python manage.py register_openline_connector \
  --connector-id my_max_connector \
  --name "MAX Connector" \
  --placement-url "https://BASE/bitrix/handler/"

python manage.py bind_openlines_events \
  --handler-url "https://BASE/bitrix/handler/"
```

`Handler already binded` — **нормально**, подписка уже есть.

### 11.2 В интерфейсе Bitrix (обязательно!)

1. **CRM → Контакт-центр → Открытые линии**
2. Открыть линию с ID = `BITRIX_OPENLINE_LINE_ID`
3. **Каналы / Коннекторы** → подключить **my_max_connector** (Telegram Bot Connector)
4. Убедиться, что линия **активна**, назначены **операторы**

Без шага 3 сообщения из MAX уйдут с `session_id=None` — диалог в ОЛ не появится.

### 11.3 Проверка

Напиши в MAX-группу. В логах Django:

```
OpenLines: Bitrix принял сообщение ... → session_id=123 chat_id=456 (connector=my_max_connector line=2)
```

`line=` должен совпадать с `BITRIX_OPENLINE_LINE_ID`.

---

## 12. Создание MAX-групп из CRM

### 12.1 Webhook робота

```
POST https://BASE/bitrix/max/create-group/?secret=MAX_GROUP_WEBHOOK_SECRET
```

Параметры (query или body):

| Параметр | Обязательно | Описание |
|----------|-------------|----------|
| `entity_type` | да | `contact` или `company` |
| `entity_id` | да | ID в Bitrix |
| `title` | нет | Если пусто — автогенерация |
| `source` | нет | Метка в логах |
| `request_id` | нет | Idempotency в логах |

Пример:

```
.../bitrix/max/create-group/?secret=123&entity_type=contact&entity_id=2&source=bitrix_robot
```

### 12.2 Автоназвание группы

Код: `openlines/views.py` → `_build_generated_group_title`.

Шаблон:

```
{номер} / LACONISTIQ / DECOPLUME {Фамилия Имя} партнер, {регион} (Профиль, Плинтус, Панели)
```

- **Номер** — из UF контакта/компании (`BITRIX_UF_*_MESSENGER_CHAT_NUMBER`)
- **ФИО** — контакт (для компании — первый связанный контакт)
- **Регион** — UF/адрес из Bitrix
- Если робот передаёт `title` — автогенерация **не** вызывается

### 12.3 Кого добавляют в группу

**Из контакта:**

1. Клиент (MAX из IMOL контакта)
2. Ответственный (`ASSIGNED_BY_ID` → MAX через `BITRIX_UF_USER_MAX_USER_ID`)
3. Сотрудники из админки с галочкой «Добавлять при создании из контакта»

**Из компании:**

1. Ответственный компании
2. Сотрудники из админки с галочкой «Добавлять при создании из компании»
3. Телефоны `PHONE` с типом `MAILING` на карточке компании

### 12.4 Связка группы с CRM (после bot_added)

По номеру в начале title (`1213 / ...`):

- **Контакт** с таким номером → IMOL `max:g:{chat_id}` + `BITRIX_UF_MAX_CHAT_ID`
- **Компания** с таким номером → `BITRIX_UF_COMPANY_MAX_CHAT_ID`

---

## 13. Рассылки

### 13.1 Админка

**Шаблоны рассылки → Запуск рассылки**

Фильтры: город, тип компании, потенциал, тип чата (личные / группы).

Перед рассылкой: **Синхронизировать из Bitrix** (слепки контактов/компаний).

### 13.2 Как система находит MAX-чаты

**Контакт:** `max_chat_id` в snapshot, IMOL в `raw_data`.

**Компания:** `max_chat_id` / `BITRIX_UF_COMPANY_MAX_CHAT_ID`, плюс IMOL связанных контактов.

Группы в MAX: chat id вида `max:-75656737192663` (отрицательный id).

### 13.3 Webhook рассылки из робота

```
POST https://BASE/api/bitrix/run-mailing/?secret=BITRIX_MAILING_WEBHOOK_SECRET
```

Параметры зависят от реализации робота (шаблон, фильтры и т.д.).

---

## 14. Админка Django

| Раздел | Назначение |
|--------|------------|
| **Openlines → Сотрудники (MAX-группы)** | Кого добавлять при создании группы (отдельно для контакта/компании) |
| **Openlines → Настройка приветствия MAX-группы** | Текст приветствия |
| **Openlines → Bitrix portals** | OAuth-токены приложения |
| **Mailings → Шаблоны рассылки** | Текст и медиа |
| **Mailings → Запуск рассылки** | Фильтры и предпросмотр |
| **Mailings → Слепки контактов/компаний Bitrix** | Sync и просмотр `max_chat_id` |
| **Mailings → Партнёры** | Зарегистрированные чаты (`max:-...`) |
| **Mailings → История рассылок** | Лог отправок |

---

## 15. Команды manage.py и scripts

### Django / Open Lines

```bash
python manage.py migrate
python manage.py collectstatic --noinput

python manage.py register_openline_connector --placement-url "https://BASE/bitrix/handler/"
python manage.py bind_openlines_events --handler-url "https://BASE/bitrix/handler/"
```

### MAX webhook

```bash
python scripts/generate_max_webhook_secret.py --register \
  --token "$MAX_BOT_TOKEN" \
  --webhook-url "https://BASE/bitrix/max/webhook/" \
  --update-types "message_created,bot_added,chat_title_changed"
```

### Браузер MAX (см. раздел 10)

```bash
# Первый вход (один раз)
python scripts/max_ws_token_keeper.py \
  --profile-dir profile/.max-manual-profile \
  --save-env --env-file profile/max_ws_token.env

# Демон на сервере (постоянно)
python scripts/max_ws_token_daemon.py \
  --profile-dir profile/.max-manual-profile \
  --env-file profile/max_ws_token.env \
  --headless --interval 300

# Открыть профиль вручную (проверка сессии)
python scripts/open_max_profile.py --profile-dir profile/.max-manual-profile
```

---

## 16. Смена домена / новый портал Bitrix

### 16.1 Новый домен сервера

1. Обновить в `.env`:
   - `ALLOWED_HOSTS`
   - `BITRIX_OPENLINE_PLACEMENT_HANDLER`
   - `BITRIX_OPENLINE_EVENTS_HANDLER`
   - `MAX_GROUP_AVATAR_URL` (если используется)
2. Перезапуск systemd-сервисов (gunicorn, celery, max-ws-token-daemon)
3. `register_openline_connector` с новым URL
4. `bind_openlines_events` (или `Handler already binded` — ок)
5. `generate_max_webhook_secret.py --register` с новым URL
6. В Bitrix: пути локального приложения → новый `{BASE}/bitrix/handler/` и `/install/`
7. SSL-сертификат на новом домене (certbot)

### 16.2 Новый портал Bitrix

1. Новый исходящий вебхук → `BITRIX_WEBHOOK_URL`
2. Новое локальное приложение → `BITRIX_PORTAL_URL`, `CLIENT_ID`, `CLIENT_SECRET`
3. Установить приложение на портал
4. Все `BITRIX_UF_*` — новые коды полей
5. `BITRIX_OPENLINE_LINE_ID` — из `imopenlines.config.list.get`
6. Заново: `register_openline_connector`, `bind_openlines_events`, привязка коннектора к линии в UI
7. Sync snapshot в админке

---

## 17. Типичные проблемы

| Симптом | Причина | Решение |
|---------|---------|---------|
| `final_ids=[]`, только бот в группе | Нет IMOL у контакта, нет MAX id у ответственного, пустой справочник сотрудников | Заполнить IM/UF, админка «Сотрудники MAX-группы», `BITRIX_UF_USER_MAX_USER_ID` |
| `session_id=None` в Open Lines | Неверный `LINE_ID` или коннектор не подключён к линии | `LINE_ID=2`, UI Контакт-центр → Каналы |
| `Handler already binded` | Подписка уже есть | Ничего не делать |
| `Method not found` для `crm.user.get` | Неверный метод | Использовать `user.get` |
| Группа с «дизайнер» вместо «партнер» | Робот передаёт готовый `title` | Убрать `title` из робота или поменять текст |
| Рассылка не видит компанию | Нет `max_chat_id` в UF / snapshot | `bot_added` sync, sync snapshot, проверить `BITRIX_UF_COMPANY_MAX_CHAT_ID` |
| `202` на create-group, но группы нет | Celery не запущен | Запустить `celery -A config worker` |
| Auth-ошибка при создании группы | Протух `MAX_WS_TOKEN` / демон браузера не работает | Запустить `max_ws_token_daemon`, перелогиниться через `max_ws_token_keeper` |
| Playwright `browser closed` на сервере | Нет `playwright install-deps` или нет демона | `playwright install chromium && playwright install-deps`, systemd для демона |
| Конфликт миграций openlines | Две ветки 0005 | `python manage.py migrate` после merge-миграций в репозитории |

---

## Быстрый старт (production на сервере)

```bash
# 1. .env: домен, BITRIX_WEBHOOK_URL, BITRIX_PORTAL_*, MAX_*, LINE_ID, UF-поля
# 2. migrate + collectstatic + superuser
# 3. playwright install chromium && playwright install-deps
# 4. Первый логин MAX: max_ws_token_keeper → profile/max_ws_token.env
# 5. systemd: gunicorn + celery + max_ws_token_daemon
# 6. nginx + SSL (certbot)
# 7. register_openline_connector + bind_openlines_events + MAX webhook --register
# 8. Bitrix UI: коннектор на открытой линии
# 9. Админка: сотрудники MAX-группы, приветствие
# 10. Робот: POST create-group → бот в группу MAX → связка в CRM
```

---

*Документ актуален для репозитория Telegram-bot-for-mailings. При изменении кода сверяй с `.env.example` и `config/settings.py`.*
