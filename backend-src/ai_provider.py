"""Fixed provider destinations and private configuration. No browser secrets."""
import json
import os
from pathlib import Path
import re

PROVIDERS = {
    "openai": {"name": "OpenAI", "base": "https://api.openai.com/v1"},
    "lunaroute": {"name": "LunaRoute", "base": "https://gw.lunaroute.com/v1"},
}
MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}")


def valid_key(key, provider):
    minimum = 11 if provider == "lunaroute" else 20
    if not isinstance(key, str) or not minimum <= len(key) <= 2048 or any(ord(c) < 33 or ord(c) > 126 for c in key):
        return False
    if provider == "lunaroute":
        return bool(re.fullmatch(r"lr_[A-Za-z0-9_-]{8,128}", key))
    return provider == "openai" and not key.startswith("lr_")


def load_config(directory):
    value = {}
    path = Path(directory) / "ai-settings.json"
    try:
        if path.is_file() and path.stat().st_size < 8192:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                value = {}
    except (OSError, ValueError):
        pass
    provider = os.environ.get("TRED_AI_PROVIDER") or value.get("provider") or ("lunaroute" if os.environ.get("LUNAROUTE_API_KEY") else "openai")
    if not isinstance(provider, str) or provider not in PROVIDERS:
        return {"provider": "invalid", "name": "ИИ", "api_key": "", "model": "", "endpoint": "", "allowed_users": []}
    prefix = "LUNAROUTE" if provider == "lunaroute" else "OPENAI"
    # An unrelated OPENAI_API_KEY must never override the saved LunaRoute key.
    key = os.environ.get(prefix + "_API_KEY") or value.get("api_key", "")
    model = os.environ.get(prefix + "_MODEL") or value.get("model", "gpt-5-mini")
    if not valid_key(key, provider):
        key = ""
    if not isinstance(model, str) or not MODEL.fullmatch(model):
        model = ""
    return {"provider": provider, **PROVIDERS[provider], "api_key": key, "model": model,
            "endpoint": PROVIDERS[provider]["base"] + "/responses", "allowed_users": value.get("allowed_users", [])}


def response_body(model, inputs, schema, *, name="tred_market_decision", max_tokens=3500):
    body = {"model": model, "store": False, "max_output_tokens": max_tokens, "input": inputs,
            "text": {"format": {"type": "json_schema", "name": name, "strict": True, "schema": schema}}}
    if model.rsplit("/", 1)[-1].startswith("gpt-5"):
        body["reasoning"] = {"effort": "low"}
    return body


class ModelReplyError(ValueError):
    def __init__(self, code, message):
        super().__init__(message + ' Вход не предложен.')
        self.code = code


def response_text(reply):
    if not isinstance(reply, dict) or reply.get("status") != "completed":
        raise ModelReplyError('model_incomplete', 'Провайдер не подтвердил завершение ответа модели.')
    output = reply.get('output')
    if not isinstance(output, list):
        raise ModelReplyError('model_format', 'Провайдер вернул неподдерживаемую оболочку ответа.')
    texts = []
    for row in output:
        if not isinstance(row, dict):
            raise ModelReplyError('model_format', 'Провайдер вернул неподдерживаемую оболочку ответа.')
        if row.get('type', 'message') != 'message':
            continue
        content = row.get('content', [])
        if not isinstance(content, list):
            raise ModelReplyError('model_format', 'Провайдер вернул неподдерживаемую оболочку ответа.')
        for item in content:
            if not isinstance(item, dict):
                raise ModelReplyError('model_format', 'Провайдер вернул неподдерживаемую оболочку ответа.')
            if item.get('type') == 'refusal':
                raise ModelReplyError('model_refusal', 'Модель отказалась формировать сценарий.')
            if item.get('type') == 'output_text':
                if not isinstance(item.get('text'), str):
                    raise ModelReplyError('model_format', 'Провайдер вернул неподдерживаемую оболочку ответа.')
                texts.append(item['text'])
    if not texts or not ''.join(texts).strip():
        raise ModelReplyError('model_empty', 'В завершённом ответе модели нет текста сценария.')
    return ''.join(texts)
