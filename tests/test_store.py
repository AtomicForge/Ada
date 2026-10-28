# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for ada/store.py. Tests marked "real" need the real SQLCipher."""
import os
import stat
import tempfile
import threading
import unittest
from unittest import mock

from ada import store
from ada.store import Store, StoreError

REAL = store._db is not None and not getattr(store._db, "STUB", False)
PASS = "korrekt-pferd-batterie"
SECRET = "GEHEIMER-SATZ-12345"


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "ada.db")

    def open(self, passphrase=PASS):
        s = Store(self.path, passphrase)
        self.addCleanup(s.close)
        return s


class OpenTests(Base):
    def test_new_database_is_private(self):
        self.open()
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_reopen_keeps_data(self):
        s = self.open()
        chat = s.create_chat("Titel", "llama3")
        s.add_message(chat, "user", "Hallo")
        s.set_memory("- Mag Podman")
        s.close()
        s = self.open()
        self.assertEqual(s.get_messages(chat), [{"role": "user", "content": "Hallo"}])
        self.assertEqual(s.get_memory(), "- Mag Podman")

    def test_bad_passphrases_are_refused_and_no_file_is_made(self):
        for bad in ("", "kurz", "x" * 1001, "abc\x00defghij", None, 12345678, b"password1"):
            with self.subTest(bad=str(bad)[:10]):
                with self.assertRaises(StoreError):
                    Store(self.path, bad)
        self.assertFalse(os.path.exists(self.path))

    def test_without_sqlcipher_nothing_is_written(self):
        with mock.patch.object(store, "_db", None):
            with self.assertRaises(StoreError) as cm:
                Store(self.path, PASS)
        self.assertIn("SQLCipher", str(cm.exception))
        self.assertFalse(os.path.exists(self.path))

    def test_plain_sqlite_without_cipher_is_refused(self):
        class Plain:  # looks like sqlite3: no cipher_version answer
            Error = Exception

            @staticmethod
            def connect(path, **kw):
                import sqlite3
                return sqlite3.connect(path, **kw)

        with mock.patch.object(store, "_db", Plain):
            with self.assertRaises(StoreError):
                Store(self.path, PASS)
        self.assertFalse(os.path.exists(self.path))

    def test_passphrase_with_quotes_works(self):
        tricky = "it's \"quoted\"; -- x"
        s = self.open(tricky)
        s.set_memory("ok")
        s.close()
        self.assertEqual(self.open(tricky).get_memory(), "ok")

    @unittest.skipUnless(REAL, "real SQLCipher needed")
    def test_real_wrong_passphrase_is_refused(self):
        self.open().close()
        with self.assertRaises(StoreError) as cm:
            Store(self.path, "falsche-passphrase")
        self.assertIn("Falsche Passphrase", str(cm.exception))
        self.open().close()  # the right one still works, the file is unharmed

    @unittest.skipUnless(REAL, "real SQLCipher needed")
    def test_real_file_is_encrypted_on_disk(self):
        s = self.open()
        chat = s.create_chat(SECRET, "m")
        s.add_message(chat, "user", SECRET)
        s.set_memory(SECRET)
        s.close()
        raw = open(self.path, "rb").read()
        self.assertFalse(raw.startswith(b"SQLite format 3"))
        self.assertNotIn(SECRET.encode(), raw)
        self.assertNotIn(b"messages", raw)

    @unittest.skipUnless(REAL, "real SQLCipher needed")
    def test_real_plain_sqlite_file_is_not_opened(self):
        import sqlite3
        c = sqlite3.connect(self.path)
        c.execute("create table meta (key, value)")
        c.commit()
        c.close()
        with self.assertRaises(StoreError):
            Store(self.path, PASS)
        self.assertTrue(os.path.exists(self.path))  # an existing file is never deleted

    def test_garbage_file_is_refused_and_kept(self):
        with open(self.path, "wb") as f:
            f.write(os.urandom(5000))
        with self.assertRaises(StoreError):
            Store(self.path, PASS)
        self.assertEqual(os.path.getsize(self.path), 5000)


class ChatTests(Base):
    def test_chats_newest_first(self):
        s = self.open()
        a = s.create_chat("A", "m")
        b = s.create_chat("B", "m")
        s.add_message(a, "user", "neu")  # a was used last
        self.assertEqual([c[0] for c in s.list_chats()], [a, b])

    def test_messages_keep_order_and_roles(self):
        s = self.open()
        c = s.create_chat("T", "m")
        for role, text in (("user", "1"), ("assistant", "2"), ("user", "3")):
            s.add_message(c, role, text)
        self.assertEqual(s.get_messages(c), [{"role": "user", "content": "1"},
                                             {"role": "assistant", "content": "2"},
                                             {"role": "user", "content": "3"}])

    def test_chats_are_separate(self):
        s = self.open()
        a, b = s.create_chat("A", "m"), s.create_chat("B", "m")
        s.add_message(a, "user", "nur a")
        self.assertEqual(s.get_messages(b), [])

    def test_rename(self):
        s = self.open()
        c = s.create_chat("alt", "m")
        s.rename_chat(c, "neu")
        self.assertEqual(s.list_chats()[0][1], "neu")

    def test_delete_removes_messages_too(self):
        s = self.open()
        c = s.create_chat("T", "m")
        s.add_message(c, "user", "x")
        s.delete_chat(c)
        self.assertEqual(s.list_chats(), [])
        self.assertEqual(s.get_messages(c), [])
        self.assertEqual(s._run("SELECT count(*) FROM messages", one=True)[0][0], 0)

    def test_message_for_missing_chat_is_refused(self):
        with self.assertRaises(StoreError):
            self.open().add_message(999, "user", "x")

    def test_bad_data_is_refused(self):
        s = self.open()
        c = s.create_chat("T", "m")
        for call in (lambda: s.create_chat("", "m"), lambda: s.create_chat("  ", "m"),
                     lambda: s.create_chat("T", ""), lambda: s.create_chat(None, "m"),
                     lambda: s.create_chat("x" * 201, "m"), lambda: s.create_chat("a\x00b", "m"),
                     lambda: s.add_message(c, "root", "x"), lambda: s.add_message(c, "user", None),
                     lambda: s.add_message(c, "user", "x" * (store.MAX_MESSAGE + 1)),
                     lambda: s.rename_chat(c, ""), lambda: s.set_memory(None),
                     lambda: s.set_memory("x" * (store.MAX_MEMORY + 1))):
            with self.assertRaises(StoreError):
                call()
        self.assertEqual(len(s.list_chats()), 1)

    def test_sql_injection_is_just_text(self):
        s = self.open()
        c = s.create_chat("x'); DROP TABLE chats;--", "m")
        s.add_message(c, "user", "'; DROP TABLE messages;--")
        self.assertEqual(s.list_chats()[0][1], "x'); DROP TABLE chats;--")
        self.assertEqual(s.get_messages(c)[0]["content"], "'; DROP TABLE messages;--")

    def test_empty_message_allowed_text_kept_exactly(self):
        s = self.open()
        c = s.create_chat("T", "m")
        text = "  Zeile 1\n\tZeile 2 <b>ä €</b>   "
        s.add_message(c, "assistant", text)
        self.assertEqual(s.get_messages(c)[0]["content"], text)


class MemoryAndStateTests(Base):
    def test_memory_starts_empty_and_can_be_replaced(self):
        s = self.open()
        self.assertEqual(s.get_memory(), "")
        s.set_memory("- a\n- b")
        s.set_memory("- c")
        self.assertEqual(s.get_memory(), "- c")
        s.set_memory("")
        self.assertEqual(s.get_memory(), "")

    def test_closed_store_refuses_everything(self):
        s = self.open()
        s.close()
        s.close()  # twice is fine
        for call in (s.list_chats, s.get_memory, lambda: s.create_chat("T", "m")):
            with self.assertRaises(StoreError):
                call()

    def test_use_from_several_threads(self):
        s = self.open()
        c = s.create_chat("T", "m")
        errors = []

        def work(n):
            try:
                for i in range(20):
                    s.add_message(c, "user", f"{n}-{i}")
            except Exception as exc:  # noqa
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(n,)) for n in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(s.get_messages(c)), 100)


if __name__ == "__main__":
    unittest.main()
