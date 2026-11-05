# SPDX-License-Identifier: GPL-3.0-or-later
"""ADA window: unlock screen first, then the chat (added in the next steps).

Rule for the whole UI: text from the model or from the internet is only ever
shown as plain text, never as GTK markup (use_markup stays off).
"""
import os
import sys

# ada.store must be imported BEFORE gi: once GTK has loaded the system's plain
# SQLite library, sqlcipher3 would silently use it and encrypt nothing.
from ada.store import MIN_PASSPHRASE, Store, StoreError  # isort: skip

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


class Window(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(title="ADA", default_width=900, default_height=650, **kwargs)
        self._store = None
        self._view = Adw.ToolbarView()
        self._view.add_top_bar(Adw.HeaderBar())
        self.set_content(self._view)
        page = UnlockPage(not os.path.exists(db_path()), self._unlocked)
        self._view.set_content(page)
        page.focus_first()
        self.connect("close-request", self._on_close)

    def _unlocked(self, store):
        self._store = store
        self._view.set_content(Gtk.Label(label="Entsperrt. Die Chats kommen im nächsten Schritt."))

    def _on_close(self, *_):
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
