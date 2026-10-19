import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
from http.server import ThreadingHTTPServer
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend import handler
from trading_core import Ledger
from test_support import signed_data


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.key=Ed25519PrivateKey.generate()
        self.ledger=Ledger(Path(self.tmp.name)/"db.sqlite3")
        self.server=ThreadingHTTPServer(("127.0.0.1",0),handler(self.ledger,public_key=self.key.public_key().public_bytes_raw()))
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)
        self.base=f"http://127.0.0.1:{self.server.server_port}"
        self.client=build_opener(ProxyHandler({}))

    def request(self,path,body=None,*,uid=101,headers=None,auth=True):
        h={"Content-Type":"application/json",**(headers or {})}
        if auth: h.setdefault("X-Telegram-Init-Data",signed_data(self.key,user_id=uid))
        req=Request(self.base+path,data=None if body is None else json.dumps(body).encode(),headers=h)
        try:
            with self.client.open(req,timeout=4) as r: return r.status,json.load(r),r.headers
        except HTTPError as e: return e.code,json.load(e),e.headers

    def test_all_signed_telegram_accounts_get_isolated_empty_cabinet(self):
        for uid in (101,202,303):
            status,state,_=self.request("/api/state",uid=uid)
            self.assertEqual(status,200)
            self.assertEqual(state["user"]["id"],uid)
            self.assertEqual(state["accounts"],[])
        self.assertEqual(self.request("/api/state",auth=False)[0],401)

    def test_tampered_identity_and_cross_origin_mutation_are_rejected(self):
        signed=signed_data(self.key,user_id=101)
        self.assertEqual(self.request("/api/state",headers={"X-Telegram-Init-Data":signed.replace("101","202")})[0],401)
        self.assertEqual(self.request("/api/settings",{"expiry":30},headers={"Origin":"https://evil.example"})[0],403)
        self.assertEqual(self.request("/api/settings",{"user_id":202})[0],400)
        status,_,headers=self.request("/api/settings",{"expiry":30},headers={"Origin":"https://kofemolka787.github.io"})
        self.assertEqual(status,200)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"),"https://kofemolka787.github.io")
        self.assertEqual(self.request("/api/state",uid=202)[1]["preferences"]["expiry"],5)

    def test_pair_exchange_read_only_ingestion_and_revocation(self):
        code=self.request("/api/pair",{})[1]["code"]
        status,result,_=self.request("/api/bridge/claim",{"code":code},auth=False)
        self.assertEqual(status,200)
        self.assertEqual(self.request("/api/bridge/claim",{"code":code},auth=False)[0],401)
        self.assertEqual(self.request("/api/bridge/events",{"events":[]},auth=False)[0],401)
        self.assertEqual(self.request("/api/bridge/events",{"events":[]},auth=False,headers={"Authorization":"Bearer "+result["token"]})[0],400)
        self.request("/api/unlink",{})
        self.assertEqual(self.request("/api/bridge/events",{"events":[{"type":"quote","symbol":"EURUSD","price":1.1,"time":1800000000}]},auth=False,headers={"Authorization":"Bearer "+result["token"]})[0],401)

    def test_health_no_trading_endpoints_and_no_path_traversal(self):
        self.assertEqual(self.request("/healthz",auth=False)[1]["real_orders_supported"],False)
        for path in ("/api/order","/api/trade","/private/ledger.sqlite3","/../private/ledger.sqlite3"):
            self.assertEqual(self.request(path,{})[0],404)


if __name__=="__main__": unittest.main()
