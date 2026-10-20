"""Local-only visible API-key input, requested by the owner. Never a web API."""
import argparse
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from openai_errors import http_error_message
from ai_provider import MODEL, PROVIDERS, response_body, response_text, valid_key


def verify_key(key, model):
    if not valid_key(key, "openai"):
        raise ValueError("Проверь API-ключ: нужна полная строка без пробелов.")
    if not isinstance(model, str) or not MODEL.fullmatch(model):
        raise ValueError("Проверь название модели. Для стандартного варианта достаточно Enter.")
    try:
        request = Request("https://api.openai.com/v1/models/" + model, headers={"Authorization": "Bearer " + key})
        with urlopen(request, timeout=20) as response:
            result = json.loads(response.read(16384))
        if not isinstance(result, dict) or not result.get("id"):
            raise ValueError("Модель не подтвердила доступ.")
    except HTTPError as error:
        raise ValueError(http_error_message(error)) from None
    except (URLError, OSError):
        raise ValueError("Нет соединения с OpenAI. Проверь интернет и повтори настройку.") from None


def read_key(provider="openai"):
    while True:
        key = input(f"{PROVIDERS[provider]['name']} API key (видимый ввод): ").strip()
        if not key:
            print("Ключ не введён. Вставь его в эту строку и только затем нажми Enter.")
        elif not valid_key(key, provider):
            print("Нужен ключ LunaRoute с префиксом lr_ без пробелов." if provider == "lunaroute" else "Нужна полная строка ключа OpenAI без пробелов. Для lr_ запусти SETUP-LUNAROUTE.cmd.")
        else:
            print(f"Введено символов ключа: {len(key)}.")
            return key


def read_model():
    while True:
        model = input("Модель [gpt-5-mini] — просто Enter для стандартной: ").strip() or "gpt-5-mini"
        if model.startswith(("sk-", "lr_")):
            print("Это строка модели, ключ здесь не нужен. Просто нажми Enter для gpt-5-mini.")
        elif not re.fullmatch(r"[A-Za-z0-9._:-]{1,80}", model):
            print("Нужно название модели, например gpt-5-mini. Повтори ввод или нажми Enter.")
        else:
            return model


def lunaroute_models(key):
    if not valid_key(key, "lunaroute"):
        raise ValueError("Нужен полный ключ LunaRoute с префиксом lr_.")
    request = Request(PROVIDERS['lunaroute']['base'] + '/models', headers={'Authorization':'Bearer ' + key})
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read(1048577)
        if len(raw) > 1048576:
            raise ValueError()
        value = json.loads(raw)
        models = []
        for row in value.get('data', []):
            if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not MODEL.fullmatch(row['id']):
                continue
            capabilities = row.get('capabilities') or {}
            if isinstance(capabilities, dict) and any(capabilities.get(k) is True for k in ('image_generation','embeddings','rerank','systemone')):
                continue
            models.append(row['id'])
        if not models:
            raise ValueError()
        return sorted(set(models))
    except HTTPError as error:
        raise ValueError(http_error_message(error, provider='LunaRoute')) from None
    except (URLError, OSError):
        raise ValueError("Нет соединения с LunaRoute. Проверь интернет и повтори настройку.") from None
    except (ValueError, TypeError, AttributeError):
        raise ValueError("LunaRoute не передал доступные языковые модели. Проверь каталог и разрешения в своём аккаунте.") from None


def read_lunaroute_model(models):
    preferred = next((m for short in ('gpt-5-mini','gpt-4.1-mini','gpt-5','gpt-4.1') for m in models if m.rsplit('/',1)[-1] == short), models[0])
    print("Доступные модели LunaRoute:")
    for index, model in enumerate(models, 1):
        print(f"{index}. {model}")
    while True:
        choice = input(f"Номер модели или её ID [Enter: {preferred}]: ").strip()
        if not choice:
            return preferred
        if choice.isascii() and choice.isdigit() and 1 <= int(choice) <= len(models):
            return models[int(choice)-1]
        if choice in models:
            return choice
        print("Выбери номер или ID модели из списка выше. Ключ в эту строку не вставляй.")


def verify_lunaroute_inference(key, model):
    schema = {'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok'],'additionalProperties':False}
    body = response_body(model, [{'role':'user','content':'Return JSON with ok set to true.'}], schema, name='tred_connection_check', max_tokens=512)
    request = Request(PROVIDERS['lunaroute']['base'] + '/responses', data=json.dumps(body).encode(), headers={'Authorization':'Bearer ' + key,'Content-Type':'application/json'})
    try:
        with urlopen(request, timeout=35) as response:
            raw = response.read(262145)
        if len(raw) > 262144:
            raise ValueError()
        verified = json.loads(response_text(json.loads(raw)))
        if not isinstance(verified, dict) or set(verified) != {'ok'} or verified['ok'] is not True:
            raise ValueError()
    except HTTPError as error:
        raise ValueError(http_error_message(error, provider='LunaRoute')) from None
    except (URLError, OSError):
        raise ValueError("Не удалось дождаться тестового ответа LunaRoute. Проверь интернет и повтори.") from None
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("Модель LunaRoute не вернула проверяемый JSON. Выбери другую доступную модель и повтори настройку.") from None


def save(directory, key, model="gpt-5-mini", *, allowed_users, provider="openai"):
    if not valid_key(key, provider):
        raise ValueError("API-ключ имеет неподдерживаемый формат.")
    if not isinstance(model, str) or not MODEL.fullmatch(model):
        raise ValueError("Некорректное название модели.")
    if not allowed_users or any(type(uid) is not int or uid <= 0 for uid in allowed_users):
        raise ValueError("Сначала свяжи свой кабинет TRED с расширением.")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=directory, mode="w", encoding="utf-8", delete=False) as f:
        temporary = Path(f.name)
        json.dump({"provider":provider, "api_key": key, "model": model, "allowed_users": sorted(set(allowed_users))}, f)
    try:
        os.chmod(temporary, 0o600)
        temporary.replace(directory / "ai-settings.json")
    finally:
        temporary.unlink(missing_ok=True)


def main():
    default = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "TRED" / "data" if os.name == "nt" else Path(__file__).resolve().parent / "private"
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=default)
    parser.add_argument("--provider", choices=tuple(PROVIDERS), default="openai")
    args = parser.parse_args()
    provider = PROVIDERS[args.provider]['name']
    print(f"TRED: локальная настройка {provider}.")
    print("Используются кредиты LunaRoute; прямой OpenAI API-аккаунт для этого не нужен." if args.provider == 'lunaroute' else "API OpenAI оплачивается отдельно от ChatGPT.")
    print("Символы ключа будут ВИДНЫ при вводе. Вставь ключ в строку ниже.")
    print("Не отправляй скриншот со строкой ключа в чат.")
    key = read_key(args.provider)
    model = read_lunaroute_model(lunaroute_models(key)) if args.provider == 'lunaroute' else read_model()
    database = args.data_dir / "ledger.sqlite3"
    if not database.is_file():
        raise ValueError("Сначала запусти TRED и свяжи свой кабинет с расширением.")
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        allowed = [r[0] for r in db.execute("SELECT user_id FROM pairing WHERE token_hash IS NOT NULL AND token_expires>?", (time.time(),))]
    if len(allowed) > 1:
        owner = input("В TRED несколько кабинетов. Введи свой Telegram ID из вкладки «Кабинет»: ").strip()
        allowed = [uid for uid in allowed if str(uid) == owner]
    if not allowed:
        raise ValueError("Сначала свяжи свой кабинет TRED с расширением.")
    if args.provider == 'lunaroute':
        print("Проверяем короткий запрос модели LunaRoute — он расходует немного кредитов.")
        verify_lunaroute_inference(key, model)
    else:
        print("Проверяем доступ к модели OpenAI…")
        verify_key(key, model)
    save(args.data_dir, key, model, allowed_users=allowed, provider=args.provider)
    print("Ключ сохранён в личной папке TRED. В приложении повтори запрос сигнала.")
    print("LunaRoute вернул проверенный ответ выбранной модели." if args.provider == 'lunaroute' else "Доступ к модели подтверждён; наличие API-кредитов эта проверка не подтверждает.")
    print(f"Ключ используется локальным сервером для HTTPS-запросов к {PROVIDERS[args.provider]['base']}.")


if __name__ == "__main__":
    try:
        main()
    except ValueError as error:
        print(str(error))  # Only fixed local messages; no provider payload/key.
        raise SystemExit(1)
    except (OSError, EOFError, KeyboardInterrupt, sqlite3.Error):
        print("Настройка не завершена. Сначала запусти TRED и свяжи свой кабинет; затем проверь ввод.")
        raise SystemExit(1)
