"""Separate browser devices + scoped CloudStorage fixture + real signed API."""
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from playwright.sync_api import expect,sync_playwright

from backend import handler
from test_support import signed_data
from trading_core import Ledger

ROOT=Path(__file__).resolve().parent
ROUTE="https://fixture-sync.trycloudflare.com"
NEW_ROUTE="https://updated-sync.trycloudflare.com"
with tempfile.TemporaryDirectory(prefix="tred-device-sync-") as temporary:
    ledger=Ledger(Path(temporary)/"ledger.sqlite3")
    token=ledger.claim(ledger.pair(101)["code"])["token"]
    def balance(amount):
        ledger.ingest(token,{"events":[{"type":"account","mode":"demo","account_id":"456","currency":"USD","balance":amount,"time":time.time()}]})
    balance("205.20")
    private=Ed25519PrivateKey.generate()
    public=private.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
    base_handler=handler(ledger,public_key=public);pair_requests=[]
    class FixtureHandler(base_handler):
        def do_GET(self):
            if self.path=="/app-config.json":return self.reply(200,{"mode":"device","version":3})
            return super().do_GET()
        def do_POST(self):
            if self.path=="/api/pair":pair_requests.append(True)
            return super().do_POST()
    server=ThreadingHTTPServer(("127.0.0.1",0),FixtureHandler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f"http://127.0.0.1:{server.server_port}"
    cloud={};cloud_values=[]
    def cloud_op(user,method,key,value=None):
        if method=="get":return cloud.get((user,key),"")
        assert method=="set" and key=="tred_backend_v1"
        decoded=json.loads(value);assert set(decoded)=={"v","owner","base"}
        assert decoded["owner"]==user
        cloud[(user,key)]=value;cloud_values.append(decoded)
        return True
    try:
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(executable_path="/usr/bin/chromium",args=["--no-sandbox","--disable-dev-shm-usage"])
            errors=[]
            try:
                def device(user,legacy=False,width=390):
                    context=browser.new_context(viewport={"width":width,"height":844})
                    context.on("page",lambda page:page.on("pageerror",lambda error:errors.append(str(error))))
                    context.expose_function("fixtureCloud",lambda method,key,value=None:cloud_op(user,method,key,value))
                    signature=signed_data(private,user_id=user)
                    init="""window.Telegram={WebApp:{initData:SIGNED,initDataUnsafe:{user:{id:USER}},ready(){},expand(){},CloudStorage:{
                      getItem(key,callback){window.fixtureCloud('get',key).then(value=>callback(null,value),()=>callback('fixture error'));},
                      setItem(key,value,callback){window.fixtureCloud('set',key,value).then(saved=>callback(null,saved),()=>callback('fixture error'));}
                    }}};""".replace("SIGNED",json.dumps(signature)).replace("USER",str(user))
                    if legacy:init+="try{if(location.origin==="+json.dumps(base)+")localStorage.setItem('tred.backend.v1.101',"+json.dumps(ROUTE)+");}catch(_){}"
                    context.add_init_script(init)
                    context.route("https://telegram.org/js/telegram-web-app.js",lambda route:route.fulfill(content_type="application/javascript",body="/* SDK fixture installed before page scripts */"))
                    def backend_route(route):
                        parsed=urlsplit(route.request.url)
                        response=route.fetch(url=base+parsed.path+("?"+parsed.query if parsed.query else ""))
                        route.fulfill(response=response)
                    context.route(ROUTE+"/**",backend_route);context.route(NEW_ROUTE+"/**",backend_route)
                    page=context.new_page();page.goto(base+"/");page.locator('[data-tab="profile"]').click()
                    return context,page
                phone_context,phone=device(101)
                expect(phone.locator("#account-list strong")).to_have_text("—")
                desktop_context,desktop=device(101,legacy=True,width=1200)
                expect(desktop.locator("#account-list strong")).to_have_text("$205.20",timeout=10000)
                expect(desktop.locator("#storage-note")).to_contain_text("сохранено в Telegram")
                assert (101,"tred_backend_v1") in cloud
                phone.bring_to_front();phone.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                expect(phone.locator("#account-list strong")).to_have_text("$205.20",timeout=10000)
                assert phone.evaluate("localStorage.getItem('tred.backend.v1.101')")==ROUTE
                balance("185.20")
                expect(phone.locator("#account-list strong")).to_have_text("$185.20",timeout=7000)
                desktop.bring_to_front();desktop.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                expect(desktop.locator("#account-list strong")).to_have_text("$185.20",timeout=7000)
                stranger_context,stranger=device(202)
                expect(stranger.locator("#account-list strong")).to_have_text("—")
                assert (202,"tred_backend_v1") not in cloud
                assert ledger.state({"id":202})["accounts"]==[]
                cloud[(101,"tred_backend_v1")]=json.dumps({"v":1,"owner":101,"base":NEW_ROUTE})
                phone.bring_to_front();phone.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                expect(phone.locator("#account-list strong")).to_have_text("$185.20")
                phone.wait_for_function("newRoute=>localStorage.getItem('tred.backend.v1.101')===newRoute",arg=NEW_ROUTE,timeout=10000)
                balance("223.40")
                expect(phone.locator("#account-list strong")).to_have_text("$223.40",timeout=7000)
                assert pair_requests==[],"Opening another device must never create a new connector pairing code"
                assert not errors,errors
                result={"desktop_legacy_route_migrated_to_scoped_cloud":True,"phone_without_local_storage_restores_on_resume":True,"two_devices_show_real_signed_api_balance":True,"live_changes_visible_after_resume":True,"new_shared_route_discovered_without_repairing":True,"other_telegram_user_empty":True,"no_new_pair_requests":True,"cloud_fields_only":["v","owner","base"],"page_errors":len(errors),"native_telegram_cloud_or_owner_phone_verified":False}
                (ROOT/"artifacts").mkdir(exist_ok=True)
                (ROOT/"artifacts/device-sync-check.json").write_text(json.dumps(result,indent=2)+"\n")
                print(json.dumps(result))
                stranger_context.close();desktop_context.close();phone_context.close()
            finally:browser.close()
    finally:server.shutdown();server.server_close();thread.join(timeout=5)
