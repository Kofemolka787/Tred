"""Bounded Responses SSE decoding; only a completed structured reply is usable."""
import json
import time

MAX_REPLY = 262144


class ResponseError(ValueError):
    pass


def read_response(response, *, deadline, clock=time.monotonic):
    headers = getattr(response, 'headers', {})
    content_type = headers.get('Content-Type', '') if headers else ''
    if 'text/event-stream' not in content_type.lower():
        raw = response.read(MAX_REPLY + 1)
        if clock() > deadline:
            raise ResponseError('ИИ превысил время ожидания ответа. Запрос не повторён автоматически.')
        if len(raw) > MAX_REPLY:
            raise ResponseError('ИИ вернул слишком большой ответ.')
        return json.loads(raw)
    total, frames, data, event = 0, 0, [], ''
    while True:
        if clock() > deadline:
            raise ResponseError('ИИ превысил время ожидания потока ответа. Запрос не повторён автоматически.')
        line = response.readline(MAX_REPLY + 1)
        if clock() > deadline:
            raise ResponseError('ИИ превысил время ожидания потока ответа. Запрос не повторён автоматически.')
        if not line:
            raise ResponseError('Поток ответа ИИ прервался до завершения. Частичный ответ не используется.')
        total += len(line)
        if total > 2097152 or len(line) > MAX_REPLY:
            raise ResponseError('Поток ответа ИИ превысил допустимый размер.')
        text = line.decode('utf-8').rstrip('\r\n')
        if text.startswith(':'):
            continue  # Heartbeat; no market facts or output are fabricated.
        if text.startswith('event:'):
            event = text[6:].strip()
        elif text.startswith('data:'):
            data.append(text[5:].lstrip(' '))
            if sum(map(len, data)) > MAX_REPLY:
                raise ResponseError('ИИ вернул слишком большой ответ.')
        elif not text and data:
            frames += 1
            if frames > 8192:
                raise ResponseError('В потоке ИИ слишком много событий.')
            payload = '\n'.join(data); data = []
            if payload == '[DONE]':
                raise ResponseError('ИИ завершил поток без проверяемого результата.')
            value = json.loads(payload)
            if not isinstance(value, dict):
                raise ResponseError('Неподдерживаемый формат потока ИИ.')
            kind = value.get('type', event); event = ''
            if kind == 'response.completed':
                return value.get('response')
            if kind in ('response.failed', 'response.incomplete', 'error'):
                raise ResponseError('Модель не завершила структурированный ответ. Частичный ответ не используется.')
            # Text/reasoning deltas are deliberately not retained or displayed.
