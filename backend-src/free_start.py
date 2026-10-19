"""Free local backend + official ephemeral HTTPS tunnel; no account or card."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import queue
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener, urlopen
from tunnel_check import can_wait_for_client,public_probe,quick_url,tunnel_command,tunnel_event

ROOT = Path(__file__).resolve().parent
RELEASE = "2026.10.1"
BINARIES = {
    ("Windows","AMD64"):("cloudflared-windows-amd64.exe","4d2490e74082c5d48dbfedaf92576fa5bd1702379ab88eb7faa0cfbe8dc891b6"),
    ("Linux","x86_64"):("cloudflared-linux-amd64","3bfc539f45d2620c71debb7a86be0efa737ed5ccbad9eb85c787116900e5c586"),
}


def tunnel_binary(directory=None):
    selected = BINARIES.get((platform.system(),platform.machine()))
    if not selected:
        raise ValueError("Автозагрузка поддерживает Windows x64 и Linux x64. Для другой системы установи официальный cloudflared вручную.")
    name, expected = selected
    tools = ROOT/"tools" if directory is None else Path(directory)
    tools.mkdir(parents=True,exist_ok=True)
    target = tools/name
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == expected:
        return target
    temp = target.with_suffix(".download")
    try:
        digest = hashlib.sha256()
        size = 0
        with urlopen(f"https://github.com/cloudflare/cloudflared/releases/download/{RELEASE}/{name}",timeout=30) as response, temp.open("wb") as out:
            while block := response.read(1024*1024):
                size += len(block)
                if size > 120*1024*1024:
                    raise ValueError("Неожиданный размер cloudflared.")
                digest.update(block); out.write(block)
        if digest.hexdigest() != expected:
            raise ValueError("Контрольная сумма cloudflared не совпала; запуск отменён.")
        temp.replace(target)
        target.chmod(0o700)
    finally:
        temp.unlink(missing_ok=True)
    return target


def check_local(port):
    client = build_opener(ProxyHandler({}))  # Only loopback; public requests retain proxy/TLS.
    base = f"http://127.0.0.1:{port}"
    try:
        with client.open(base+"/healthz",timeout=2) as response:
            h = json.load(response)
            if h.get("service") != "tred-backend" or h.get("api_version") != 1:
                return False
        try:
            client.open(base+"/api/state",timeout=2).close()
        except HTTPError as error:
            return error.code == 401
        return False
    except (URLError,OSError,ValueError):
        return False


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--port",type=int,default=8790)
    parser.add_argument("--local-only",action="store_true")
    parser.add_argument("--check",action="store_true",help="Start, verify health/private API, then stop own process")
    parser.add_argument("--data-dir",type=Path,default=ROOT/"private")
    args=parser.parse_args()
    if not 1024 <= args.port <= 65535:
        raise ValueError("Некорректный порт.")
    if check_local(args.port):
        raise ValueError("TRED уже работает на этом порту. Используй открытое окно запуска; второй сервер не нужен.")
    args.data_dir.mkdir(parents=True,exist_ok=True)
    # A previous verified URL belongs to the previous ephemeral route.
    (args.data_dir/"server-url.txt").unlink(missing_ok=True)
    # Verify/download before starting the API, so failures leave no stray process.
    executable = None if args.local_only else tunnel_binary(args.data_dir/"tools")
    backend = subprocess.Popen([sys.executable,"-B","-u",str(ROOT/"backend.py"),"--port",str(args.port),"--database",str(args.data_dir/"ledger.sqlite3")],cwd=ROOT)
    tunnel = None
    try:
        deadline=time.monotonic()+15
        while not check_local(args.port):
            if backend.poll() is not None or time.monotonic()>deadline:
                raise ValueError("Не удалось запустить API. Проверь сообщение сервера выше.")
            time.sleep(.2)
        print("TRED API: OK. Без подписи Telegram личные данные недоступны.",flush=True)
        if args.check:
            print(json.dumps({"local_api_verified":True,"public_tunnel_verified":False}))
            return
        if args.local_only:
            print("Локальный режим. Для телефона нужен HTTPS-туннель.",flush=True)
            backend.wait()
            return
        print("Подключаем внешний адрес Cloudflare по HTTP/2…",flush=True)
        tunnel=subprocess.Popen(tunnel_command(executable,args.port),cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding="utf-8",errors="replace")
        output=queue.Queue(maxsize=256)
        diagnostic={"protocol":"http2","stage":"waiting_for_address","url_assigned":False,"channel":"WAITING","https":"NOT_CHECKED"}
        def record():
            target=args.data_dir/"tunnel-diagnostics.json"
            target.write_text(json.dumps(diagnostic,indent=2)+"\n",encoding="utf-8")
        def consume(line):
            if line is not None:
                event=tunnel_event(line)
                if event:diagnostic["channel"]=event
        def drain():
            while True:
                try:consume(output.get_nowait())
                except queue.Empty:break
        def put(line):
            try:output.put_nowait(line)
            except queue.Full:
                try:output.get_nowait()
                except queue.Empty:pass
                try:output.put_nowait(line)
                except queue.Full:pass
        def read():
            for line in tunnel.stdout:
                put(line[:4000])
            put(None)
        threading.Thread(target=read,daemon=True).start()
        deadline=time.monotonic()+60
        url=None
        while time.monotonic()<deadline:
            try: line=output.get(timeout=1)
            except queue.Empty: continue
            if line is None: break
            consume(line)
            found=quick_url(line)
            if found: url=found; break
        if not url:
            drain();diagnostic["exit_code"]=tunnel.poll();record()
            raise ValueError("Cloudflare не выдал адрес. Канал: "+diagnostic["channel"]+". Диагностика: "+str(args.data_dir/"tunnel-diagnostics.json"))
        # A URL in stdout alone does not establish an operational public route.
        diagnostic.update(stage="checking_https",url_assigned=True)
        print("Адрес выдан. Проверяем HTTPS-доступ; результат будет виден ниже.",flush=True)
        for attempt in range(18):
            drain()
            if tunnel.poll() is not None:break
            diagnostic["https"]=public_probe(url)
            if diagnostic["https"]=="OK":break
            if attempt%3==0:
                print("Проверка HTTPS: "+diagnostic["https"]+"; канал: "+diagnostic["channel"],flush=True)
            time.sleep(1)
        drain()
        if can_wait_for_client(diagnostic["https"],diagnostic["channel"]) and tunnel.poll() is None and backend.poll() is None:
            diagnostic["stage"]="waiting_for_client_dns";record()
            print("\nТуннель подключён. Windows пока не находит IP-адрес сервера.",flush=True)
            print("Адрес для проверки с телефона (доступ пока НЕ подтверждён):\n"+url,flush=True)
            print("В Telegram на телефоне: TRED → Кабинет → Подключить кабинет.\nВставь адрес выше. Приложение само проверит HTTPS и подпись Telegram.\nЕсли не подключится, попробуй на телефоне мобильный интернет вместо Wi-Fi.\nОставь это окно открытым. Ctrl+C — остановить сервер.",flush=True)
            previous=diagnostic["https"]
            # Keep the existing route alive while a client on another network
            # performs its own full HTTPS + signed identity verification.
            while tunnel.poll() is None and backend.poll() is None:
                time.sleep(5)
                drain()
                if tunnel.poll() is not None or backend.poll() is not None:break
                diagnostic["https"]=public_probe(url)
                record()
                if diagnostic["https"]=="OK":break
                if diagnostic["https"]!=previous:
                    print("Проверка с ПК: "+diagnostic["https"]+"; канал: "+diagnostic["channel"],flush=True)
                    previous=diagnostic["https"]
                if not can_wait_for_client(diagnostic["https"],diagnostic["channel"]):break
        if diagnostic["https"]!="OK" or tunnel.poll() is not None or backend.poll() is not None:
            drain();diagnostic["exit_code"]=tunnel.poll();record()
            raise ValueError("HTTPS-доступ не подтверждён: "+diagnostic["https"]+"; канал: "+diagnostic["channel"]+". Диагностика: "+str(args.data_dir/"tunnel-diagnostics.json"))
        diagnostic["stage"]="ready";record()
        (args.data_dir/"server-url.txt").write_text(url+"\n",encoding="utf-8")
        print("\nВ кабинете TRED нажми «Подключить кабинет» и вставь:\n"+url,flush=True)
        print("Этот же адрес и одноразовый код укажи в модуле браузера.\nНе закрывай это окно, компьютер и вкладку Pocket Option.\nПосле нового запуска адрес может измениться. Ctrl+C — остановить.",flush=True)
        # Drain logs without retaining or displaying arbitrary tunnel output.
        while tunnel.poll() is None and backend.poll() is None:
            try:consume(output.get(timeout=1))
            except queue.Empty: pass
        diagnostic["stage"]="connection_closed";diagnostic["exit_code"]=tunnel.poll();record()
        raise ValueError("Связь завершилась. Новые данные не поступают; запусти TRED заново.")
    finally:
        for process in (tunnel,backend):
            if process is not None and process.poll() is None:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)


if __name__=="__main__":
    try: main()
    except KeyboardInterrupt: print("TRED остановлен.")
    except Exception as error:
        print(str(error) if isinstance(error,ValueError) else "Запуск не выполнен: "+type(error).__name__,file=sys.stderr)
        raise SystemExit(1)
