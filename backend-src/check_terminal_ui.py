"""Browser DOM -> actual worker -> signed API -> actual Mini App, on fixtures.

This does not load an unpacked extension or authenticate to Pocket Option.
"""
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import tempfile
import threading

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from playwright.sync_api import expect,sync_playwright

from backend import handler
from test_support import signed_data
from trading_core import Ledger

ROOT=Path(__file__).resolve().parent
HTML=b'''<!doctype html><html><body>
<div class="balance-info-block"><div><span id="label">QT Demo USD</span></div><div id="amount">105.20</div></div>
<div hidden><span>QT Real USD</span><div>99999.00</div></div>
<div>Order amount <input value="20"></div><div>Payout 91% +38.20</div>
</body></html>'''

with tempfile.TemporaryDirectory(prefix="tred-terminal-ui-") as temporary:
    ledger=Ledger(Path(temporary)/"ledger.sqlite3")
    token=ledger.claim(ledger.pair(101)["code"])["token"]
    private=Ed25519PrivateKey.generate()
    public=private.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
    base_handler=handler(ledger,public_key=public)
    class FixtureHandler(base_handler):
        def do_GET(self):
            if self.path.startswith("/ru/cabinet/"):
                return self.reply(200,HTML,"text/html; charset=utf-8")
            if self.path=="/app-config.json":
                return self.reply(200,{"mode":"api","version":3})
            return super().do_GET()
    server=ThreadingHTTPServer(("127.0.0.1",0),FixtureHandler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f"http://127.0.0.1:{server.server_port}"
    worker=subprocess.Popen(["node","check_connector_worker.js","--bridge",base,"--fixture-token",token],cwd=ROOT,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
    lock=threading.Lock();counter=0
    def forward(message):
        global counter
        with lock:
            counter+=1
            worker.stdin.write(json.dumps({"id":counter,"message":message})+"\n");worker.stdin.flush()
            line=worker.stdout.readline()
            if not line:raise RuntimeError("Fixture worker stopped")
            result=json.loads(line)
            assert result["id"]==counter
            return result["result"]
    try:
        assert json.loads(worker.stdout.readline())=={"ready":True}
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(executable_path="/usr/bin/chromium",args=["--no-sandbox","--disable-dev-shm-usage"])
            try:
                context=browser.new_context();errors=[]
                context.on("page",lambda page:page.on("pageerror",lambda error:errors.append(str(error))))
                terminal=context.new_page();terminal.expose_function("forwardToWorker",forward)
                terminal.add_init_script("window.chrome={runtime:{id:'fixture',sendMessage:message=>{if(window.invalidateRelay)throw Error('Extension context invalidated.');return window.forwardToWorker(message);}}};")
                terminal.goto(base+"/ru/cabinet/demo-quick-high-low/")
                terminal.evaluate((ROOT/"connector/balance-read.js").read_text())
                assert terminal.evaluate("TredTerminalBalance.read().snapshot.balance")=="105.20"
                terminal.evaluate((ROOT/"connector/relay.js").read_text())
                terminal.wait_for_function("__tredRelay.active")
                # No WebSocket/account event is supplied anywhere in this test.
                terminal.wait_for_timeout(700)
                assert ledger.state({"id":101})["accounts"][0]["balance"]=="105.20"

                signed=signed_data(private)
                context.route("https://telegram.org/js/telegram-web-app.js",lambda route:route.fulfill(content_type="application/javascript",body="window.Telegram={WebApp:{initData:"+json.dumps(signed)+",initDataUnsafe:{user:{id:101}},ready(){},expand(){}}};"))
                app=context.new_page();app.goto(base+"/")
                app.locator('[data-tab="profile"]').click()
                expect(app.locator("#account-list strong")).to_have_text("$105.20")
                amounts=["205.20","185.20","223.40"]
                for amount in amounts:
                    terminal.evaluate("amount=>document.querySelector('#amount').textContent=amount",amount)
                    expect(app.locator("#account-list strong")).to_have_text("$"+amount,timeout=7000)
                    assert ledger.state({"id":101})["accounts"][0]["balance"]==amount
                assert ledger.state({"id":202})["accounts"]==[]
                status=forward({"action":"status"})
                assert status["lastBalance"]["origin"]=="terminal-dom"

                terminal.evaluate("window.invalidateRelay=true;window.postMessage({source:'tred-observer-v1',observer_version:'0.3.13',events:[{type:'quote'}]},location.origin)")
                terminal.wait_for_function("!__tredRelay.active")
                terminal.evaluate("window.invalidateRelay=false")
                terminal.evaluate((ROOT/"connector/relay.js").read_text())
                terminal.wait_for_function("__tredRelay.active")
                terminal.evaluate("document.querySelector('#amount').textContent='201.05'")
                expect(app.locator("#account-list strong")).to_have_text("$201.05",timeout=7000)

                # Read-only parser edge cases, with delivery stopped.
                terminal.evaluate("__tredRelay.dispose()")
                terminal.evaluate("document.querySelector('#amount').textContent='12,345.67'")
                assert terminal.evaluate("TredTerminalBalance.read().snapshot.balance")=="12345.67"
                terminal.evaluate("document.querySelector('#label').textContent='QT Demo RUB'")
                assert terminal.evaluate("TredTerminalBalance.read().code")=="unsupported_currency"
                terminal.evaluate("document.querySelector('#label').textContent='QT Demo USD';document.body.insertAdjacentHTML('beforeend','<div><span>QT Real USD</span><div>500.00</div></div>')")
                assert terminal.evaluate("TredTerminalBalance.read().code")=="ambiguous_header"
                assert not errors,errors
                result={"actual_dom_mutation_to_worker_to_backend_to_signed_miniapp":True,"no_websocket_balance_events_provided":True,"balances":["105.20",*amounts,"201.05"],"no_id_confirmation_or_page_reload":True,"synchronous_context_invalidation_recovered":True,"hidden_real_account_ignored":True,"ambiguous_header_and_non_usd_rejected":True,"second_user_empty":True,"page_errors":len(errors),"native_extension_or_owner_device_verified":False}
                (ROOT/"artifacts").mkdir(exist_ok=True)
                (ROOT/"artifacts/terminal-balance-check.json").write_text(json.dumps(result,indent=2)+"\n")
                print(json.dumps(result))
            finally:browser.close()
    finally:
        if worker.stdin:worker.stdin.close()
        try:worker.wait(timeout=5)
        except subprocess.TimeoutExpired:worker.terminate();worker.wait(timeout=5)
        server.shutdown();server.server_close();thread.join(timeout=5)
