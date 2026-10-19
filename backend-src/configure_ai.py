"""Local-only hidden API-key input. Never part of the Mini App API."""
import argparse
import getpass
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
    if not isinstance(key, str) or not 20 <= len(key) <= 2048 or any(ord(c) < 33 for c in key) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,80}", model):
        raise ValueError("Проверь формат ключа и название модели.")
    try:
        request = Request("https://api.openai.com/v1/models/" + model, headers={"Authorization": "Bearer " + key})
        with urlopen(request, timeout=20) as response:
            result = json.loads(response.read(16384))
        if not isinstance(result, dict) or not result.get("id"):
            raise ValueError("Модель не подтвердила доступ.")
    except HTTPError as error:
        raise ValueError("OpenAI не подтвердил ключ или доступ к модели. Проверь API-аккаунт и название модели.") from None
    except (URLError, OSError):
        raise ValueError("Нет соединения с OpenAI. Проверь интернет и повтори настройку.") from None


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
    print("Введи API-ключ здесь. Символы не отображаются; в чат его отправлять не нужно.")
    key = getpass.getpass("OpenAI API key: ").strip()
    model = input("Модель [gpt-5-mini]: ").strip() or "gpt-5-mini"
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
