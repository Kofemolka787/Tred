"""Functional compatibility check against the actual unchanged backend API."""
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time

from backend import handler
from trading_core import DomainError,Ledger


with tempfile.TemporaryDirectory(prefix="tred-bridge-check-") as temporary:
    ledger=Ledger(Path(temporary)/"ledger.sqlite3")
    token=ledger.claim(ledger.pair(101)["code"])["token"]
    now=time.time()
    # Reproduce the original all-or-nothing rejection with an unfiltered batch.
    try:
        ledger.ingest(token,{"events":[{"type":"account","mode":"demo","account_id":"456","currency":"USD","balance":"100.00","time":now},{"type":"quote","symbol":"EURUSD","price":1.1,"time":now+30}]})
        raise AssertionError("Future quote should have failed strict validation")
    except DomainError as error:
        assert str(error)=="Некорректное время данных."
    assert ledger.state({"id":101})["accounts"]==[]
    server=ThreadingHTTPServer(("127.0.0.1",0),handler(ledger))
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    try:
        subprocess.run(["node","check_connector_worker.js","--api",f"http://127.0.0.1:{server.server_port}","--fixture-token",token],check=True)
        state=ledger.state({"id":101})
        assert state["accounts"][0]["balance"]=="100.00"
        assert state["accounts"][0]["mode"]=="demo"
        assert state["connection"]["live"]
        assert ledger.state({"id":202})["accounts"]==[]
        print(json.dumps({"original_failure_reproduced":True,"filtered_mixed_batch_account_and_live_quote_accepted":True,"second_user_empty":True}))
    finally:
        server.shutdown();server.server_close();worker.join(timeout=5)
