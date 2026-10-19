import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from package_backend import build
from windows_setup import launcher_lock,prepare


class UserStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        build(self.root/"download")
        self.source=self.root/"download"/"backend-src"
        self.base=self.root/"personal profile"/"TRED"

    def source_contents(self):
        return {p.relative_to(self.source).as_posix():p.read_bytes() for p in self.source.rglob("*") if p.is_file()}

    def test_prepare_writes_only_personal_storage_and_repeats_safely(self):
        before=self.source_contents()
        app,data=prepare(self.source,self.base)
        self.assertTrue(app.is_relative_to(self.base))
        self.assertTrue(data.is_relative_to(self.base))
        self.assertTrue((app/"backend.py").is_file())
        (data/"settings-sentinel").write_text("keep")
        self.assertEqual(prepare(self.source,self.base),(app,data))
        self.assertEqual((data/"settings-sentinel").read_text(),"keep")
        self.assertEqual(self.source_contents(),before)
        self.assertFalse((self.source/".venv").exists())
        self.assertFalse((self.source/"private").exists())

    def test_modified_download_is_rejected_before_application_is_copied(self):
        (self.source/"backend.py").write_text("changed")
        with self.assertRaises(ValueError):prepare(self.source,self.base)
        self.assertFalse((self.base/"app").exists())

    def test_manifest_cannot_escape_personal_application_directory(self):
        p=self.source/"SHA256SUMS.json";manifest=json.loads(p.read_text())
        manifest["../outside.py"]="0"*64;p.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):prepare(self.source,self.base)
        self.assertFalse((self.base/"app").exists())

    def test_consistent_previous_database_is_preserved_without_overwrite(self):
        old=self.source/"private"/"ledger.sqlite3";old.parent.mkdir()
        with sqlite3.connect(old) as db:
            db.execute("CREATE TABLE sample(value TEXT)");db.execute("INSERT INTO sample VALUES ('previous')")
        _,data=prepare(self.source,self.base)
        with sqlite3.connect(data/"ledger.sqlite3") as db:
            self.assertEqual(db.execute("SELECT value FROM sample").fetchone()[0],"previous")
            db.execute("UPDATE sample SET value='newer'")
        prepare(self.source,self.base)
        with sqlite3.connect(data/"ledger.sqlite3") as db:
            self.assertEqual(db.execute("SELECT value FROM sample").fetchone()[0],"newer")

    def test_second_launcher_cannot_modify_environment_while_first_is_running(self):
        with launcher_lock(self.base):
            with self.assertRaises(ValueError):
                with launcher_lock(self.base):self.fail("Second launch acquired lock")
        with launcher_lock(self.base):pass


if __name__=="__main__":unittest.main()
