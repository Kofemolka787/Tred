"""Regression checks for the downloaded launcher that failed in real cmd.exe."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from package_backend import VERSION, build, windows_batch


class WindowsPackageTests(unittest.TestCase):
    def test_batch_normalizes_unix_lines_without_losing_commands(self):
        unix=b'@echo off\ncd /d "%~dp0"\n".venv\\Scripts\\python.exe" "free_start.py"\n'
        dos=windows_batch(unix)
        self.assertEqual(dos.replace(b"\r\n",b"\n"),unix)
        self.assertEqual(windows_batch(dos),dos)

    def test_non_ascii_and_malformed_batch_files_are_rejected(self):
        for data in ("echo Ошибка\n".encode(),b"\xef\xbb\xbf@echo off\n",b"@echo off\rbroken\n",b"@echo off\0\n"):
            with self.subTest(data=data),self.assertRaises(ValueError):
                windows_batch(data)

    def test_downloaded_archive_has_ascii_crlf_launcher_and_matching_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive=build(tmp)
            self.assertEqual(archive.read_bytes(),(Path(tmp)/f"tred-backend-v{VERSION}.zip").read_bytes())
            with zipfile.ZipFile(archive) as z:
                data=z.read("TredBackend/START-WINDOWS.cmd")
                data.decode("ascii")
                self.assertGreater(data.count(b"\r\n"),20)
                self.assertNotIn(b"\n",data.replace(b"\r\n",b""))
                self.assertNotIn(b"\r",data.replace(b"\r\n",b""))
                self.assertIn(b'cd /d "%~dp0"\r\n',data)
                self.assertIn(b'".venv\\Scripts\\python.exe" "free_start.py"\r\n',data)
                manifest=json.loads(z.read("TredBackend/SHA256SUMS.json"))
                self.assertEqual(manifest["START-WINDOWS.cmd"],hashlib.sha256(data).hexdigest())
                self.assertEqual((Path(tmp)/"backend-src/START-WINDOWS.cmd").read_bytes(),data)


if __name__=="__main__":unittest.main()
