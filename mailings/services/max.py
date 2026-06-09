"""
Базовый клиент MAX API для отправки сообщений.
"""
import mimetypes
import os
import time
import tempfile

import requests
from django.conf import settings


class MaxApiError(Exception):
    pass


def _api_base_url() -> str:
    return (getattr(settings, 'MAX_API_BASE_URL', '') or '').rstrip('/')


def _bot_token() -> str:
    token = (getattr(settings, 'MAX_BOT_TOKEN', '') or '').strip()
    if not token:
        raise MaxApiError('MAX_BOT_TOKEN не задан в настройках/.env')
    return token


def send_message(chat_id: str, text: str) -> dict:
    """
    Отправка текстового сообщения в MAX.
    """
    if not text:
        return {'ok': True, 'skipped': True}

    base_url = _api_base_url()
    if not base_url:
        raise MaxApiError('MAX_API_BASE_URL не задан в настройках/.env')

    token = _bot_token()
    url = f'{base_url}/messages'
    payload = {
        'text': text,
    }
    headers = {
        'Authorization': token,
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }

    # MAX ожидает получателя как query-параметр (chat_id/user_id), а не в body.
    response = requests.post(url, params={'chat_id': str(chat_id)}, json=payload, headers=headers, timeout=30)
    data = response.json() if response.content else {}
    if response.ok:
        return {'ok': True, 'result': data}

    # Фолбэк для сценариев, когда во внешней системе хранится user_id, а не chat_id.
    if isinstance(data, dict) and data.get('code') in ('proto.payload', 'dialog.not.found', 'chat.not.found'):
        response_user = requests.post(url, params={'user_id': str(chat_id)}, json=payload, headers=headers, timeout=30)
        data_user = response_user.json() if response_user.content else {}
        if response_user.ok:
            return {'ok': True, 'result': data_user}
        raise MaxApiError(f'MAX API {response_user.status_code}: {data_user or response_user.text}')

    raise MaxApiError(f'MAX API {response.status_code}: {data or response.text}')


def _upload_type_by_send_type(send_type: str) -> str:
    """
    Преобразование внутреннего типа отправки в тип MAX /uploads.
    """
    if send_type == 'photo':
        return 'image'
    if send_type == 'video':
        return 'video'
    return 'file'


def _extract_token(data: dict) -> str:
    """
    Извлекает token из разных форматов ответа MAX upload.
    """
    if not isinstance(data, dict):
        return ''

    token = data.get('token')
    if isinstance(token, str) and token:
        return token

    payload = data.get('payload')
    if isinstance(payload, dict):
        token = payload.get('token')
        if isinstance(token, str) and token:
            return token

    for bucket_key in ('photos', 'files', 'videos', 'audios'):
        bucket = data.get(bucket_key)
        if isinstance(bucket, dict):
            for _, item in bucket.items():
                if isinstance(item, dict):
                    token = item.get('token')
                    if isinstance(token, str) and token:
                        return token

    # На случай иных вложенных структур.
    for _, value in data.items():
        if isinstance(value, dict):
            nested = _extract_token(value)
            if nested:
                return nested
    return ''


def _build_attachment_payload(upload_type: str, init_data: dict, upload_data: dict) -> dict:
    """
    Для image/file MAX рекомендует использовать весь upload response как payload,
    а для video/audio достаточно token.
    """
    if upload_type in ('image', 'file') and isinstance(upload_data, dict) and upload_data:
        return upload_data

    media_token = _extract_token(upload_data) or _extract_token(init_data)
    if not media_token:
        raise MaxApiError(f'MAX upload: не найден token в ответе: {upload_data or init_data}')
    return {'token': media_token}


def send_media_message(chat_id: str, file_path: str, send_type: str = 'document', text: str = '') -> dict:
    """
    Отправка медиа в MAX через flow:
    1) POST /uploads?type=...
    2) upload файла по полученному URL
    3) POST /messages?chat_id=... с attachments[].payload.token
    """
    if not os.path.isfile(file_path):
        raise MaxApiError(f'Файл не найден: {file_path}')

    base_url = _api_base_url()
    if not base_url:
        raise MaxApiError('MAX_API_BASE_URL не задан в настройках/.env')
    token = _bot_token()

    headers = {
        'Authorization': token,
        'Accept': 'application/json',
    }
    upload_type = _upload_type_by_send_type(send_type)

    init_resp = requests.post(
        f'{base_url}/uploads',
        params={'type': upload_type},
        headers=headers,
        timeout=30,
    )
    init_data = init_resp.json() if init_resp.content else {}
    if not init_resp.ok:
        raise MaxApiError(f'MAX uploads init {init_resp.status_code}: {init_data or init_resp.text}')

    upload_url = init_data.get('url')
    if not upload_url:
        raise MaxApiError(f'MAX uploads init: нет url в ответе: {init_data}')

    filename = os.path.basename(file_path) or 'upload.bin'
    mime_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'

    def _try_upload(*, with_auth: bool, field_name: str = 'data', explicit_tuple: bool = True) -> tuple[bool, dict, int, str]:
        with open(file_path, 'rb') as f:
            headers_upload = {}
            if with_auth:
                headers_upload['Authorization'] = token
            files_payload = (
                {field_name: (filename, f, mime_type)}
                if explicit_tuple
                else {field_name: f}
            )
            resp = requests.post(
                upload_url,
                headers=headers_upload,
                files=files_payload,
                timeout=180,
            )
        try:
            data_resp = resp.json() if resp.content else {}
        except ValueError:
            data_resp = {}
        return resp.ok, data_resp, resp.status_code, resp.text

    upload_data = {}
    upload_ok = False
    upload_status = 0
    upload_text = ''
    # Идем сначала по официальному пути из доки MAX: multipart field `data`.
    # Старые варианты оставляем как fallback на случай несовместимых upload-endpoint'ов.
    for field_name, with_auth, explicit_tuple in (
        ('data', True, True),
        ('data', False, True),
        ('data', True, False),
        ('data', False, False),
        ('file', True, True),
        ('file', False, True),
        ('photo', True, True),
        ('image', True, True),
        ('document', True, True),
    ):
        ok, data_resp, status_code, text_resp = _try_upload(
            with_auth=with_auth,
            field_name=field_name,
            explicit_tuple=explicit_tuple,
        )
        upload_data, upload_ok, upload_status, upload_text = data_resp, ok, status_code, text_resp
        if ok:
            break
    if not upload_ok:
        raise MaxApiError(f'MAX upload {upload_status}: {upload_data or upload_text}')

    payload = {
        'attachments': [
            {
                'type': upload_type,
                'payload': _build_attachment_payload(upload_type, init_data, upload_data),
            }
        ]
    }
    if text:
        payload['text'] = text

    def _send_with_recipient(recipient_key: str) -> dict:
        last_data_local = {}
        for attempt in range(3):
            msg_resp = requests.post(
                f'{base_url}/messages',
                params={recipient_key: str(chat_id)},
                json=payload,
                headers={**headers, 'Content-Type': 'application/json'},
                timeout=30,
            )
            last_data_local = msg_resp.json() if msg_resp.content else {}
            if msg_resp.ok:
                return {'ok': True, 'result': last_data_local}
            if isinstance(last_data_local, dict) and last_data_local.get('code') == 'attachment.not.ready' and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            return {
                'ok': False,
                'status_code': msg_resp.status_code,
                'data': last_data_local,
                'text': msg_resp.text,
            }
        return {'ok': False, 'data': last_data_local, 'text': str(last_data_local)}

    result_chat = _send_with_recipient('chat_id')
    if result_chat.get('ok'):
        return result_chat

    data_chat = result_chat.get('data') if isinstance(result_chat, dict) else {}
    if isinstance(data_chat, dict) and data_chat.get('code') in ('dialog.not.found', 'proto.payload', 'chat.not.found'):
        result_user = _send_with_recipient('user_id')
        if result_user.get('ok'):
            return result_user
        raise MaxApiError(
            f"MAX messages {result_user.get('status_code', '')}: {result_user.get('data') or result_user.get('text')}"
        )

    raise MaxApiError(
        f"MAX messages {result_chat.get('status_code', '')}: {result_chat.get('data') or result_chat.get('text')}"
    )


def extract_chat_link(chat_payload: dict) -> str:
    """Ссылка на чат / приглашение из ответа GET /chats/{chatId}."""
    if not isinstance(chat_payload, dict):
        return ''
    link = chat_payload.get('link')
    if isinstance(link, str) and link.strip():
        return link.strip()
    chat = chat_payload.get('chat')
    if isinstance(chat, dict):
        nested = chat.get('link')
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
    return ''


def get_chat_invite_link(chat_id: str) -> str:
    """Получить invite-ссылку группы, если MAX её отдаёт в поле link."""
    return extract_chat_link(get_chat(chat_id))


def get_chat(chat_id: str) -> dict:
    """
    Получить информацию о чате MAX.
    """
    base_url = _api_base_url()
    if not base_url:
        raise MaxApiError('MAX_API_BASE_URL не задан в настройках/.env')
    token = _bot_token()

    response = requests.get(
        f'{base_url}/chats/{chat_id}',
        headers={
            'Authorization': token,
            'Accept': 'application/json',
        },
        timeout=30,
    )
    data = response.json() if response.content else {}
    if not response.ok:
        raise MaxApiError(f'MAX chat.get {response.status_code}: {data or response.text}')
    return data


def get_chat_title(chat_id: str) -> str:
    """
    Получить актуальное название чата/канала в MAX.
    """
    data = get_chat(chat_id)
    for key in ('title', 'name', 'display_name'):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    chat = data.get('chat')
    if isinstance(chat, dict):
        for key in ('title', 'name', 'display_name'):
            val = chat.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ''


def send_media_message_by_url(chat_id: str, file_url: str, send_type: str = 'document', text: str = '') -> dict:
    """
    Отправка медиа в MAX по внешней ссылке:
    скачиваем во временный файл и используем стандартный upload flow.
    """
    if not file_url:
        raise MaxApiError('Пустой file_url')

    resp = requests.get(file_url, timeout=60)
    if not resp.ok:
        raise MaxApiError(f'Не удалось скачать файл по URL ({resp.status_code}): {file_url}')

    suffix = '.bin'
    if send_type == 'photo':
        suffix = '.jpg'
    elif send_type == 'video':
        suffix = '.mp4'

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(resp.content)
        tmp_path = tmp.name
    try:
        return send_media_message(chat_id=chat_id, file_path=tmp_path, send_type=send_type, text=text)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def set_chat_icon(chat_id: str, icon_url: str, notify: bool = False) -> dict:
    """
    Изменить иконку группового чата MAX через PATCH /chats/{chatId}.
    icon_url должен быть публичным https URL.
    """
    normalized_chat_id = str(chat_id or '').strip()
    normalized_icon_url = str(icon_url or '').strip()
    if not normalized_chat_id:
        raise MaxApiError('chat_id is empty')
    if not normalized_icon_url:
        raise MaxApiError('icon_url is empty')
    if not normalized_icon_url.startswith('https://'):
        raise MaxApiError('icon_url must start with https://')

    base_url = _api_base_url()
    if not base_url:
        raise MaxApiError('MAX_API_BASE_URL не задан в настройках/.env')
    token = _bot_token()

    response = requests.patch(
        f'{base_url}/chats/{normalized_chat_id}',
        headers={
            'Authorization': token,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        },
        json={
            'icon': {'url': normalized_icon_url},
            'notify': bool(notify),
        },
        timeout=30,
    )
    data = response.json() if response.content else {}
    if not response.ok:
        raise MaxApiError(f'MAX chat.patch {response.status_code}: {data or response.text}')
    return {'ok': True, 'result': data}


def add_chat_members(chat_id: str, user_ids: list[int]) -> dict:
    """
    Добавить участников в групповой чат MAX через официальный API:
      POST /chats/{chatId}/members
    """
    normalized_chat_id = str(chat_id or '').strip()
    if not normalized_chat_id:
        raise MaxApiError('chat_id is empty')

    prepared_ids: list[int] = []
    seen: set[int] = set()
    for value in user_ids or []:
        try:
            uid = int(value)
        except (TypeError, ValueError):
            continue
        if uid <= 0 or uid in seen:
            continue
        seen.add(uid)
        prepared_ids.append(uid)

    if not prepared_ids:
        return {'ok': True, 'skipped': True, 'reason': 'empty user_ids'}

    base_url = _api_base_url()
    if not base_url:
        raise MaxApiError('MAX_API_BASE_URL не задан в настройках/.env')
    token = _bot_token()

    response = requests.post(
        f'{base_url}/chats/{normalized_chat_id}/members',
        headers={
            'Authorization': token,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        },
        json={'user_ids': prepared_ids},
        timeout=30,
    )
    data = response.json() if response.content else {}
    if not response.ok:
        raise MaxApiError(f'MAX chat.members.add {response.status_code}: {data or response.text}')
    return {'ok': True, 'result': data}
