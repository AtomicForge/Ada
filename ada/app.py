# SPDX-License-Identifier: GPL-3.0-or-later
"""ADA window: unlock screen first, then the chat (added in the next steps).

Rule for the whole UI: text from the model or from the internet is only ever
shown as plain text, never as GTK markup (use_markup stays off).
"""
import os
import sys
import threading

# ada.store must be imported BEFORE gi: once GTK has loaded the system's plain
# SQLite library, sqlcipher3 would silently use it and encrypt nothing.
from ada.store import MIN_PASSPHRASE, Store, StoreError  # isort: skip
from ada.ollama import DEFAULT_URL, ChatStream, OllamaError, list_models  # isort: skip

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

APP_ID = "io.github.AtomicForge.Ada"


def db_path():
    folder = os.path.join(GLib.get_user_data_dir(), "ada")
    os.makedirs(folder, mode=0o700, exist_ok=True)
    return os.path.join(folder, "ada.db")


class UnlockPage(Gtk.Box):
    """Ask for the passphrase (twice when the database is created)."""

    def __init__(self, new, on_unlock):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                         halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, width_request=320)
        self._new = new
        self._on_unlock = on_unlock
        self.append(Gtk.Label(label="ADA", css_classes=["title-1"]))
        hint = (f"Neue Passphrase wählen (mindestens {MIN_PASSPHRASE} Zeichen). "
                "Ohne sie sind deine Chats nicht wiederherstellbar." if new
                else "Passphrase eingeben, um die Chats zu entsperren.")
        self.append(Gtk.Label(label=hint, wrap=True, justify=Gtk.Justification.CENTER))
        self._entry = Gtk.PasswordEntry(show_peek_icon=True, placeholder_text="Passphrase")
        self._entry.connect("activate", self._go)
        self.append(self._entry)
        self._again = None
        if new:
            self._again = Gtk.PasswordEntry(show_peek_icon=True, placeholder_text="Passphrase wiederholen")
            self._again.connect("activate", self._go)
            self.append(self._again)
        self._error = Gtk.Label(wrap=True, css_classes=["error"])
        self.append(self._error)
        button = Gtk.Button(label="Anlegen" if new else "Entsperren", css_classes=["suggested-action"])
        button.connect("clicked", self._go)
        self.append(button)

    def focus_first(self):
        self._entry.grab_focus()

    def _go(self, *_):
        passphrase = self._entry.get_text()
        if self._new and passphrase != self._again.get_text():
            self._error.set_text("Die beiden Eingaben sind verschieden.")
            return
        try:
            store = Store(db_path(), passphrase)
        except StoreError as exc:
            self._error.set_text(str(exc))
            return
        self._entry.set_text("")
        if self._again:
            self._again.set_text("")
        self._on_unlock(store)


class ChatPage(Gtk.Box):
    """One chat: the text so far, a model chooser, an input line, Send/Stop.
    The text is shown in a TextView, which never interprets markup."""

    def __init__(self, store):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                         margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        self._store = store
        self._chat_id = None
        self._stream = None
        self._answer = []
        self._text = Gtk.TextView(editable=False, cursor_visible=False, left_margin=6, right_margin=6,
                                  wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self._buffer = self._text.get_buffer()
        self._end = self._buffer.create_mark("end", self._buffer.get_end_iter(), False)
        self.append(Gtk.ScrolledWindow(vexpand=True, child=self._text))
        self._status = Gtk.Label(xalign=0, wrap=True, css_classes=["error"])
        self.append(self._status)
        self._models = Gtk.StringList()
        row = Gtk.Box(spacing=6)
        self._dropdown = Gtk.DropDown(model=self._models)
        row.append(self._dropdown)
        self._entry = Gtk.Entry(hexpand=True, placeholder_text="Nachricht")
        self._entry.connect("activate", self._send)
        row.append(self._entry)
        self._button = Gtk.Button(label="Senden", css_classes=["suggested-action"])
        self._button.connect("clicked", self._on_button)
        row.append(self._button)
        self.append(row)
        threading.Thread(target=self._load_models, daemon=True).start()

    def stop(self):
        if self._stream:
            self._stream.cancel()

    def _load_models(self):
        try:
            names = list_models(DEFAULT_URL)
            error = "" if names else "Ollama hat keine Modelle installiert."
        except OllamaError as exc:
            names, error = [], str(exc)
        GLib.idle_add(self._models_loaded, names, error)

    def _models_loaded(self, names, error):
        for name in names:
            self._models.append(name)
        self._status.set_text(error)

    def _on_button(self, *_):
        if self._stream:
            self._stream.cancel()
        else:
            self._send()

    def _show(self, text):
        self._buffer.insert(self._buffer.get_end_iter(), text)
        self._text.scroll_to_mark(self._end, 0.0, False, 0.0, 0.0)

    def _send(self, *_):
        text = self._entry.get_text().strip()
        item = self._dropdown.get_selected_item()
        if self._stream or not text:
            return
        if item is None:
            self._status.set_text("Kein Modell verfügbar. Läuft Ollama?")
            return
        model = item.get_string()
        try:
            if self._chat_id is None:
                self._chat_id = self._store.create_chat(text[:40], model)
            self._store.add_message(self._chat_id, "user", text)
            messages = self._store.get_messages(self._chat_id)
        except StoreError as exc:
            self._status.set_text(str(exc))
            return
        self._status.set_text("")
        self._entry.set_text("")
        self._entry.set_sensitive(False)
        self._button.set_label("Stopp")
        self._answer = []
        self._show(f"Du: {text}\n\nADA: ")
        self._stream = ChatStream(DEFAULT_URL, model, messages)
        threading.Thread(target=self._run, args=(self._stream,), daemon=True).start()

    def _run(self, stream):
        error = ""
        try:
            for piece in stream:
                GLib.idle_add(self._piece, piece)
        except OllamaError as exc:
            error = str(exc)
        GLib.idle_add(self._finished, error)

    def _piece(self, piece):
        self._answer.append(piece)
        self._show(piece)

    def _finished(self, error):
        answer = "".join(self._answer)
        try:
            if answer:
                self._store.add_message(self._chat_id, "assistant", answer)
        except StoreError as exc:
            error = error or str(exc)
        self._show("\n\n")
        self._status.set_text(error)
        self._stream = None
        self._button.set_label("Senden")
        self._entry.set_sensitive(True)
        self._entry.grab_focus()


class Window(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(title="ADA", default_width=900, default_height=650, **kwargs)
        self._store = None
        self._chat = None
        self._view = Adw.ToolbarView()
        self._view.add_top_bar(Adw.HeaderBar())
        self.set_content(self._view)
        page = UnlockPage(not os.path.exists(db_path()), self._unlocked)
        self._view.set_content(page)
        page.focus_first()
        self.connect("close-request", self._on_close)

    def _unlocked(self, store):
        self._store = store
        self._chat = ChatPage(store)
        self._view.set_content(self._chat)

    def _on_close(self, *_):
        if self._chat:
            self._chat.stop()
        if self._store:
            self._store.close()
        return False


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)

    def do_activate(self):
        (self.props.active_window or Window(application=self)).present()


def main():
    return App().run(sys.argv)
