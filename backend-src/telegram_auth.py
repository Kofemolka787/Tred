"""Verify Telegram Mini App identity using Telegram's public Ed25519 key."""
import base64
import json
import time
from urllib.parse import parse_qsl
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

BOT_ID = 8957984384
PUBLIC_KEY = bytes.fromhex("e7bf03a2fa4602af4580703d88dda5bb59f32ed8b02a56c187fe7d34caed242d")


def verify(value, *, public_key=PUBLIC_KEY, now=None):
    now = time.time() if now is None else now
    try:
        if not isinstance(value, str) or not value or len(value) > 16000:
            raise ValueError()
        pairs = parse_qsl(value, keep_blank_values=True, strict_parsing=True, max_num_fields=32)
        data = dict(pairs)
        if len(data) != len(pairs):
            raise ValueError()
        encoded = data["signature"]
        signature = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        message = f"{BOT_ID}:WebAppData\n" + "\n".join(f"{k}={data[k]}" for k in sorted(data) if k not in {"hash", "signature"})
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, message.encode())
        if not now - 3600 <= int(data["auth_date"]) <= now + 30:
            raise ValueError()
        user = json.loads(data["user"])
        if not isinstance(user, dict) or type(user.get("id")) is not int or not 0 < user["id"] < 2**53 or user.get("is_bot"):
            raise ValueError()
        return {"id": user["id"], "first_name": str(user.get("first_name", ""))[:80]}
    except (ValueError, KeyError, TypeError, InvalidSignature, UnicodeError):
        raise ValueError("Открой приложение заново из Telegram.") from None
