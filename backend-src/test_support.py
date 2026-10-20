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

def wire_decision(decision, payload):
    """Synthetic provider output uses the actual request's evidence IDs."""
    value = {k: decision[k] for k in ('action', 'symbol', 'direction', 'summary', 'risk', 'evidence')}
    if 'evidence_refs' in payload:
        refs = {(r['symbol'], r['period'], r['time']): r['ref'] for r in payload['evidence_refs']}
        value['evidence'] = [{'ref': refs[(e['symbol'], e['period'], e['time'])],
                              'explanation': e['explanation']} for e in decision['evidence']]
    return value


class FixtureAI:
    """Structured model contract fixture; never contacts an AI provider."""
    def __init__(self):
        self.tasks = {}
        self.calls = 0

    def status(self, uid=None):
        return {"configured": uid in (None, 101), "provider": "OpenAI", "model": "fixture-model", "market_only": True}

    def get(self, job_id):
        return self.tasks.get(job_id)

    def submit(self, job_id, snapshot):
        from concurrent.futures import Future
        if job_id not in self.tasks:
            self.calls += 1
            future = Future()
            qualified = snapshot["qualified"]
            selected = qualified[0] if qualified else None
            facts = next((p for p in snapshot["pairs"] if selected and p["symbol"] == selected["symbol"]), None)
            evidence = [{"symbol": selected["symbol"], "period": frame["period"], "time": frame["bar_time"], "explanation": "Подтверждённая структура и реакция на зону на этом периоде."} for frame in facts["frames"]] if facts else []
            decision = {"action": "enter" if selected else "wait", "symbol": selected["symbol"] if selected else "", "direction": selected["direction"] if selected else "none",
                        "summary": "Согласованная структура и подтверждённая реакция." if selected else "Нет подтверждённой реакции на зону.", "risk": "На OTC наблюдаются только цены брокера; результат не гарантирован.", "evidence": evidence,
                        "provider": "OpenAI", "model": "fixture-model", "elapsed_ms": 1, "reviewed_pairs": len(snapshot["pairs"]), "batches": 1}
            if 'evidence_refs' in snapshot:
                decision = {**decision, **wire_decision(decision, snapshot)}
            future.set_result({"snapshot": snapshot, "decision": decision})
            self.tasks[job_id] = future
        return self.tasks[job_id]

