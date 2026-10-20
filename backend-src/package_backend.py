"""Build a deterministic, allowlisted public source package, never local state."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parent
FILES=("backend.py","telegram_auth.py","trading_core.py","market_analysis.py","signal_sessions.py","market_feed.py","market_scanner.py","check_market_scan.js","test_ai_market.py","check_ai_scan_ui.py","ai_market.py","ai_provider.py","ai_decision.py","test_ai_decision.py","ai_response.py","test_ai_response.py","check_ai_stream_http.py","openai_errors.py","configure_ai.py","test_ai_provider.py","test_configure_ai.py","SETUP-AI.cmd","SETUP-LUNAROUTE.cmd","test_market_feed.py","check_market_pipeline.js","check_quote_recovery.py","check_fast_collection.py","check_market_feed_ui.py","free_start.py",
       "requirements-web.txt","START-WINDOWS.cmd","START-LINUX.sh","CONNECT-RU.md",
       "test_backend.py","test_support.py","test_trading_core.py","test_market_analysis.py","test_signal_sessions.py","check_signal_ui.py","check_connector.js",
       "package_backend.py","test_windows_package.py","windows_setup.py","test_windows_setup.py",
       "tunnel_check.py","test_tunnel_check.py","test_free_start.py","check_connector_worker.js","check_connector_http.py","check_pairing_ui.py","check_connector_popup.py","check_data_client.js","check_terminal_ui.py","check_device_sync.py")
CONNECTOR=("market-scan.js","scan-worker.js","manifest.json","observer.js","balance-read.js","relay.js","worker.js","time-check.js","account-check.js","popup.html","popup.css","popup.js")
WEB=("index.html","styles.css","app.js","signals.js","data-client.js","app-config.json")
VERSION="0.3.22"


def windows_batch(data):
    """cmd.exe must receive plain ASCII and DOS line endings, including in ZIPs."""
    text=data.decode("ascii").replace("\r\n","\n")
    if "\r" in text or "\0" in text:
        raise ValueError("Invalid Windows batch file encoding or line endings")
    return (text.rstrip("\n").replace("\n","\r\n")+"\r\n").encode("ascii")


def build(destination):
    destination=Path(destination)
    contents={name:(ROOT/name).read_bytes() for name in FILES}
    for name in contents:
        if name.endswith(".cmd"):
            contents[name]=windows_batch(contents[name])
    contents.update({"connector/"+name:(ROOT/"connector"/name).read_bytes() for name in CONNECTOR})
    contents.update({"web/"+name:(ROOT/"web"/name).read_bytes() for name in WEB})
    contents[".gitignore"]=b"private/\n.venv/\ntools/\n__pycache__/\n*.log\nserver-url.txt\n"
    contents["SHA256SUMS.json"]=(json.dumps({name:hashlib.sha256(data).hexdigest() for name,data in sorted(contents.items())},indent=2)+"\n").encode()
    source=destination/"backend-src"
    source.mkdir(parents=True,exist_ok=True)
    if source.is_symlink() or any(p.is_symlink() for p in source.rglob("*")):
        raise ValueError("Generated source directory contains a symlink")
    unexpected={p.relative_to(source).as_posix() for p in source.rglob("*") if p.is_file()}-contents.keys()
    if unexpected:
        raise ValueError("Generated source directory contains unexpected files; refusing publication")
    for name,data in contents.items():
        target=source/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    archive=destination/"tred-backend.zip"
    with zipfile.ZipFile(archive,"w",compression=zipfile.ZIP_DEFLATED) as output:
        for name,data in sorted(contents.items()):
            entry=zipfile.ZipInfo("TredBackend/"+name,date_time=(2026,10,19,0,0,0))
            entry.compress_type=zipfile.ZIP_DEFLATED
            entry.external_attr=(0o100755 if name.endswith(".sh") else 0o100644)<<16
            output.writestr(entry,data)
    with zipfile.ZipFile(archive) as check:
        if check.testzip() is not None:
            raise ValueError("Backend archive integrity check failed")
    (destination/f"tred-backend-v{VERSION}.zip").write_bytes(archive.read_bytes())
    return archive
