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


def verify_key(key, model):
    if not isinstance(key, str) or not 20 <= len(key) <= 2048 or any(ord(c) < 33 for c in key):
        raise ValueError("Проверь API-ключ: нужна полная строка без пробелов.")
    if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,80}", model):
        raise ValueError("Проверь название модели. Для стандартного варианта достаточно Enter.")
    try:
        request = Request("https://api.openai.com/v1/models/" + model, headers={"Authorization": "Bearer " + key})
        with urlopen(request, timeout=20) as response:
            result = json.loads(response.read(16384))
        if not isinstance(result, dict) or not result.get("id"):
            raise ValueError("Модель не подтвердила доступ.")
    except HTTPError as error:
        messages = {
            401: "OpenAI отклонил API-ключ (HTTP 401). Проверь полный ключ OpenAI и не был ли он отозван.",
            403: "OpenAI запретил доступ (HTTP 403). Проверь ограничения API-аккаунта и разрешения ключа.",
            404: "OpenAI не нашёл выбранную модель или не дал к ней доступ (HTTP 404). Проверь ID модели и доступ в API-аккаунте.",
            429: "OpenAI ограничил запросы или исчерпана квота (HTTP 429). Проверь API-баланс и лимиты.",
        }
        raise ValueError(messages.get(error.code, f"Проверка OpenAI завершилась ошибкой HTTP {error.code}. Повтори позже.")) from None
    except (URLError, OSError):
        raise ValueError("Нет соединения с OpenAI. Проверь интернет и повтори настройку.") from None


def read_key():
    while True:
        key = input("OpenAI API key (видимый ввод): ").strip()
        if not key:
            print("Ключ не введён. Вставь его в эту строку и только затем нажми Enter.")
        elif not 20 <= len(key) <= 2048 or any(ord(c) < 33 for c in key):
            print("Нужна полная строка API-ключа без пробелов. Повтори ввод.")
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


def save(directory, key, model="gpt-5-mini", *, allowed_users):
    if not isinstance(key, str) or not 20 <= len(key) <= 2048 or any(ord(c) < 33 for c in key):
        raise ValueError("API-ключ имеет неподдерживаемый формат.")
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,80}", model):
        raise ValueError("Некорректное название модели.")
    if not allowed_users or any(type(uid) is not int or uid <= 0 for uid in allowed_users):
        raise ValueError("Сначала свяжи свой кабинет TRED с расширением.")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=directory, mode="w", encoding="utf-8", delete=False) as f:
        temporary = Path(f.name)
        json.dump({"api_key": key, "model": model, "allowed_users": sorted(set(allowed_users))}, f)
    try:
        os.chmod(temporary, 0o600)
        temporary.replace(directory / "ai-settings.json")
    finally:
        temporary.unlink(missing_ok=True)


def main():
    default = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "TRED" / "data" if os.name == "nt" else Path(__file__).resolve().parent / "private"
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=default)
    args = parser.parse_args()
    print("TRED: локальная настройка OpenAI. API оплачивается отдельно от ChatGPT.")
    print("Символы ключа будут ВИДНЫ при вводе. Вставь ключ в строку ниже.")
    print("Не отправляй скриншот со строкой ключа в чат.")
    key = read_key()
    model = read_model()
    database = args.data_dir / "ledger.sqlite3"
    if not database.is_file():
        raise ValueError("Сначала запусти TRED и свяжи свой кабинет с расширением.")
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        allowed = [r[0] for r in db.execute("SELECT user_id FROM pairing WHERE token_hash IS NOT NULL AND token_expires>?", (time.time(),))]
    if len(allowed) > 1:
        owner = input("В TRED несколько кабинетов. Введи свой Telegram ID из вкладки «Кабинет»: ").strip()
        allowed = [uid for uid in allowed if str(uid) == owner]
    print("Проверяем доступ к модели OpenAI…")
    verify_key(key, model)
    save(args.data_dir, key, model, allowed_users=allowed)
    print("Ключ сохранён в личной папке TRED. В приложении повтори запрос сигнала.")
    print("Ключ используется только локальным сервером для HTTPS-запросов к api.openai.com.")


if __name__ == "__main__":
    try:
        main()
    except ValueError as error:
        print(str(error))  # Only fixed local messages; no provider payload/key.
        raise SystemExit(1)
    except (OSError, EOFError, KeyboardInterrupt, sqlite3.Error):
        print("Настройка не завершена. Сначала запусти TRED и свяжи свой кабинет; затем проверь ввод.")
        raise SystemExit(1)
