"""Install/run from per-user storage even when the extracted ZIP is read-only."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
import subprocess
import sys
import tempfile

SOURCE=Path(__file__).resolve().parent


def user_directory():
    value=os.environ.get("LOCALAPPDATA")
    base=Path(value) if value else Path.home()/"AppData"/"Local"
    return base/"TRED"


@contextmanager
def launcher_lock(base):
    base.mkdir(parents=True,exist_ok=True)
    with (base/"launcher.lock").open("a+b") as lock:
        lock.seek(0,2)
        if lock.tell()==0:
            lock.write(b"1");lock.flush()
        lock.seek(0)
        try:
            if os.name=="nt":
                import msvcrt
                msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            raise ValueError("TRED уже запускается или работает. Используй первое открытое окно.") from None
        try:
            yield
        finally:
            lock.seek(0)
            if os.name=="nt":
                import msvcrt
                msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(lock,fcntl.LOCK_UN)


def prepare(source,base):
    """Validate before copying. Never write to or delete the extracted source."""
    source,base=Path(source),Path(base)
    manifest_bytes=(source/"SHA256SUMS.json").read_bytes()
    manifest=json.loads(manifest_bytes)
    required={"backend.py","trading_core.py","telegram_auth.py","free_start.py","requirements-web.txt","web/index.html"}
    if not isinstance(manifest,dict) or not required <= manifest.keys() or len(manifest)>100:
        raise ValueError("Архив неполный. Распакуй весь TredBackend из новой версии ZIP.")
    contents={}
    for name,expected in manifest.items():
        if not isinstance(name,str) or not isinstance(expected,str):
            raise ValueError("Некорректный список файлов пакета.")
        relative=PurePosixPath(name)
        if not name or relative.as_posix()=="." or relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name:
            raise ValueError("Некорректный путь в пакете.")
        data=(source/relative).read_bytes()
        if hashlib.sha256(data).hexdigest()!=expected:
            raise ValueError("Файлы архива изменены или повреждены. Скачай и распакуй новую версию.")
        contents[name]=data
    app=base/"app"/hashlib.sha256(manifest_bytes).hexdigest()[:16]
    app.mkdir(parents=True,exist_ok=True)
    for name,data in contents.items():
        target=app/name
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists() and target.read_bytes()==data:
            continue
        with tempfile.NamedTemporaryFile(dir=target.parent,delete=False) as out:
            temporary=Path(out.name)
            out.write(data)
        try:
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    (app/"SHA256SUMS.json").write_bytes(manifest_bytes)
    data_dir=base/"data"
    data_dir.mkdir(parents=True,exist_ok=True)
    previous=source/"private"/"ledger.sqlite3"
    database=data_dir/"ledger.sqlite3"
    # Preserve existing records with SQLite's consistent online backup, including WAL.
    # A restart/update never overwrites an already migrated per-user database.
    if previous.is_file() and not database.exists():
        with tempfile.NamedTemporaryFile(dir=data_dir,delete=False) as out:
            temporary=Path(out.name)
        try:
            with sqlite3.connect(previous.resolve().as_uri()+"?mode=ro",uri=True) as old,sqlite3.connect(temporary) as new:
                old.backup(new)
            temporary.replace(database)
        finally:
            temporary.unlink(missing_ok=True)
    return app,data_dir


def run(source=SOURCE,base=None,*,check=False,port=8790):
    if sys.version_info < (3,12):
        raise ValueError("Установи Python 3.12 или новее, затем повтори запуск.")
    base=user_directory() if base is None else Path(base)
    with launcher_lock(base):
        app,data_dir=prepare(source,base)
        environment=base/"python"/f"{sys.version_info.major}.{sys.version_info.minor}"
        python=environment/("Scripts/python.exe" if os.name=="nt" else "bin/python")
        print("TRED: рабочие файлы сохраняются в личной папке пользователя.",flush=True)
        if not python.is_file():
            subprocess.run([sys.executable,"-m","venv",str(environment)],check=True,cwd=base)
        subprocess.run([str(python),"-m","pip","install","--disable-pip-version-check","--only-binary=:all:","--require-hashes","-r",str(app/"requirements-web.txt")],check=True,cwd=base)
        command=[str(python),"-B",str(app/"free_start.py"),"--data-dir",str(data_dir),"--port",str(port)]
        if check:
            command += ["--local-only","--check"]
        return subprocess.run(command,cwd=app).returncode


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--check",action="store_true")
    parser.add_argument("--port",type=int,default=8790)
    parser.add_argument("--storage",type=Path,help="Optional explicit personal working directory")
    args=parser.parse_args()
    try:
        raise SystemExit(run(base=args.storage,check=args.check,port=args.port))
    except PermissionError as error:
        print("Windows запретила запись в личную рабочую папку: "+str(error.filename),file=sys.stderr)
        raise SystemExit(1)
    except (ValueError,OSError,subprocess.CalledProcessError) as error:
        print(str(error),file=sys.stderr)
        raise SystemExit(1)
