"""Isolated test signing helper. Never used to authenticate production requests."""
import base64
import json
import time
from urllib.parse import urlencode
from telegram_auth import BOT_ID


def signed_data(key, user_id=101, *, stamp=None, first_name="Тест", bot_id=BOT_ID):
    fields = {"auth_date": str(int(time.time() if stamp is None else stamp)),
              "user": json.dumps({"id": user_id, "first_name": first_name}, ensure_ascii=False, separators=(",", ":")),
              "query_id": "test-query"}
    payload = f"{bot_id}:WebAppData\n" + "\n".join(f"{name}={fields[name]}" for name in sorted(fields))
    fields["signature"] = base64.urlsafe_b64encode(key.sign(payload.encode())).decode().rstrip("=")
    fields["hash"] = "unused-in-ed25519-test"
    return urlencode(fields)

