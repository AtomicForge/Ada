# SPDX-License-Identifier: GPL-3.0-or-later
"""Encrypted storage (SQLCipher) for chats and the memory text.

No GTK imports here. The whole database file is encrypted with the owner's
passphrase; no key is ever stored. If SQLCipher is missing we stop: we never
fall back to a plain, unencrypted SQLite file.
"""
import os
import threading
import time

try:
    from sqlcipher3 import dbapi2 as _db
except ImportError:  # pragma: no cover
    _db = None

MIN_PASSPHRASE = 8
MAX_PASSPHRASE = 1000
MAX_TITLE = 200
MAX_MESSAGE = 1024 * 1024
MAX_MEMORY = 20000
ROLES = ("user", "assistant", "system")
_SCHEMA_VERSION = 1

_MSG_NO_CIPHER = "SQLCipher fehlt oder ist nicht aktiv. ADA speichert nichts unverschlüsselt."
_MSG_BAD_PASSPHRASE = f"Die Passphrase muss {MIN_PASSPHRASE} bis {MAX_PASSPHRASE} Zeichen lang sein."
_MSG_WRONG = "Falsche Passphrase, oder die Datei ist keine ADA-Datenbank."
_MSG_CLOSED = "Die Datenbank ist geschlossen."
_MSG_BAD_DATA = "Ungültige Daten."

_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE chats (
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, model TEXT NOT NULL,
    created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    role TEXT NOT NULL, content TEXT NOT NULL, created REAL NOT NULL);
CREATE INDEX messages_chat ON messages(chat_id, id);
CREATE TABLE memory (id INTEGER PRIMARY KEY CHECK (id = 1), text TEXT NOT NULL);
"""


class StoreError(Exception):
    """Any storage problem. The message is safe to show to the user."""


def _check_passphrase(passphrase):
    if not (isinstance(passphrase, str) and MIN_PASSPHRASE <= len(passphrase) <= MAX_PASSPHRASE
            and "\x00" not in passphrase):
        raise StoreError(_MSG_BAD_PASSPHRASE)


def _text(value, limit, empty_ok=False):
    if not isinstance(value, str) or len(value) > limit or "\x00" in value \
            or not (value.strip() or empty_ok):
        raise StoreError(_MSG_BAD_DATA)
    return value


class Store:
    """One open, unlocked database. Safe to use from several threads."""

    def __init__(self, path, passphrase):
        """Open the database, or create it if the file does not exist yet."""
        if _db is None:
            raise StoreError(_MSG_NO_CIPHER)
        _check_passphrase(passphrase)
        path = os.fspath(path)
        new = not os.path.exists(path)
        if new:  # private from the first byte
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        self._lock = threading.Lock()
        self._conn = None
        try:
            conn = _db.connect(path, check_same_thread=False)
            self._conn = conn
            conn.execute("PRAGMA key = '%s'" % passphrase.replace("'", "''"))
            version = conn.execute("PRAGMA cipher_version").fetchone()
            if not version or not version[0]:
                raise StoreError(_MSG_NO_CIPHER)
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA secure_delete = ON")
            tables = conn.execute("SELECT name FROM sqlite_master").fetchall()  # fails if the key is wrong
            if new:
                conn.executescript(_SCHEMA)
                conn.execute("INSERT INTO meta VALUES ('schema', ?)", (str(_SCHEMA_VERSION),))
                conn.execute("INSERT INTO memory VALUES (1, '')")
                conn.commit()
            else:
                row = conn.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone() if tables else None
                if not row or row[0] != str(_SCHEMA_VERSION):
                    raise StoreError(_MSG_WRONG)
        except StoreError:
            self._abort(path, new)
            raise
        except _db.Error:
            self._abort(path, new)
            raise StoreError(_MSG_WRONG) from None

    def _abort(self, path, new):
        self.close()
        if new:  # do not leave an empty file behind
            try:
                os.remove(path)
            except OSError:
                pass

    def close(self):
        with self._lock:
            conn, self._conn = self._conn, None
        if conn is not None:
            conn.close()

    def _run(self, sql, args=(), write=False, one=False):
        with self._lock:
            if self._conn is None:
                raise StoreError(_MSG_CLOSED)
            try:
                cur = self._conn.execute(sql, args)
                rows = cur.fetchone() if one else cur.fetchall()
                if write:
                    self._conn.commit()
                return rows, cur.lastrowid
            except _db.Error:
                self._conn.rollback()
                raise StoreError(_MSG_BAD_DATA) from None

    def create_chat(self, title, model):
        now = time.time()
        _, chat_id = self._run("INSERT INTO chats (title, model, created, updated) VALUES (?, ?, ?, ?)",
                               (_text(title, MAX_TITLE), _text(model, 200), now, now), write=True)
        return chat_id

    def list_chats(self):
        """[(id, title, model, updated)] newest first."""
        rows, _ = self._run("SELECT id, title, model, updated FROM chats ORDER BY updated DESC, id DESC")
        return rows

    def rename_chat(self, chat_id, title):
        self._run("UPDATE chats SET title = ? WHERE id = ?", (_text(title, MAX_TITLE), chat_id), write=True)

    def delete_chat(self, chat_id):
        self._run("DELETE FROM chats WHERE id = ?", (chat_id,), write=True)

    def add_message(self, chat_id, role, content):
        if role not in ROLES:
            raise StoreError(_MSG_BAD_DATA)
        now = time.time()
        _, msg_id = self._run("INSERT INTO messages (chat_id, role, content, created) VALUES (?, ?, ?, ?)",
                              (chat_id, role, _text(content, MAX_MESSAGE, empty_ok=True), now), write=True)
        self._run("UPDATE chats SET updated = ? WHERE id = ?", (now, chat_id), write=True)
        return msg_id

    def get_messages(self, chat_id):
        """[{"role", "content"}] oldest first, ready for ollama.ChatStream."""
        rows, _ = self._run("SELECT role, content FROM messages WHERE chat_id = ? ORDER BY id", (chat_id,))
        return [{"role": r, "content": c} for r, c in rows]

    def get_memory(self):
        row, _ = self._run("SELECT text FROM memory WHERE id = 1", one=True)
        return row[0]

    def set_memory(self, text):
        self._run("UPDATE memory SET text = ? WHERE id = 1", (_text(text, MAX_MEMORY, empty_ok=True),), write=True)
