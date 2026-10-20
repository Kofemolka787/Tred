"""Read only allowlisted OpenAI error codes; never expose provider messages."""
import json


def http_error_message(error, *, provider="OpenAI"):
    status = error.code
    if provider == "LunaRoute":
        messages = {
            400: "LunaRoute не принял формат запроса к выбранной модели (HTTP 400). Выбери другую модель через SETUP-LUNAROUTE.cmd; требуется Responses API со структурированным JSON.",
            401: "LunaRoute отклонил ключ (HTTP 401). В SETUP-LUNAROUTE.cmd нужен действующий ключ LunaRoute с префиксом lr_.",
            402: "LunaRoute: недостаточно кредитов (HTTP 402). Проверь баланс LunaRoute в app.lunaroute.com.",
            403: "LunaRoute запретил доступ (HTTP 403). Проверь разрешения ключа, организации и выбранной модели.",
            404: "LunaRoute не нашёл выбранную модель или маршрут (HTTP 404). Повтори SETUP-LUNAROUTE.cmd и выбери модель из полученного каталога.",
            429: "LunaRoute ограничил запросы или квоту (HTTP 429). Проверь кредиты и лимиты LunaRoute в app.lunaroute.com; затем повтори один запрос.",
        }
        return messages.get(status, f"LunaRoute временно недоступен (HTTP {status}). Повтори позже.")
    if status == 429:
        code = None
        try:
            raw = error.read(16385)
            value = json.loads(raw) if len(raw) <= 16384 else None
            detail = value.get("error") if isinstance(value, dict) else None
            if isinstance(detail, dict):
                codes = (detail.get("code"), detail.get("type"))
                code = next((c for c in ("insufficient_quota", "rate_limit_exceeded") if c in codes), None)
        except (OSError, ValueError, TypeError):
            pass
        if code == "insufficient_quota":
            return "OpenAI: API-квота исчерпана (HTTP 429, insufficient_quota). Проверь кредиты и оплату проекта API в Billing. Подписка ChatGPT не оплачивает API. Изменение времени сделки не поможет."
        if code == "rate_limit_exceeded":
            return "OpenAI: временный лимит запросов или токенов (HTTP 429, rate_limit_exceeded). Подожди минуту и повтори один запрос; проверь Limits проекта API. Это не отказ стратегии и не признак отсутствия входа."
        return "OpenAI отклонил запрос с HTTP 429 без распознанного кода причины. Проверь API Billing и Limits: по этому ответу нельзя отличить нехватку кредитов от временного лимита."
    messages = {
        401: "OpenAI отклонил API-ключ (HTTP 401). Проверь полный ключ OpenAI и не был ли он отозван.",
        403: "OpenAI запретил доступ (HTTP 403). Проверь ограничения API-аккаунта и разрешения ключа.",
        404: "OpenAI не нашёл выбранную модель или не дал к ней доступ (HTTP 404). Проверь ID модели и доступ в API-аккаунте.",
    }
    return messages.get(status, f"OpenAI временно недоступен (HTTP {status}). Повтори запрос позже.")
