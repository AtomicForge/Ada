# Security

**ADA is experimental and has not been audited.** It was written with AI assistance and tested by its maintainer. Treat it accordingly.

## What ADA protects

Chats, messages, chat titles, the memory text and settings stay **unreadable when the database file leaves your machine**, for example through backups, file sync or copying the file.

- All content is stored in one SQLCipher 4 database with the SQLCipher defaults for key derivation and cipher. ADA never lowers them.
- The key is derived from a **passphrase that you type at every start** (at least 12 characters). It is not stored in a file, in the keyring or in settings. A wrong passphrase gives one generic error.
- ADA refuses to run without SQLCipher. It never falls back to plain SQLite.
- Temporary data stays in memory (`temp_store = MEMORY`), deleted data is overwritten (`secure_delete = ON`).
- The data folder has mode `0700`, the database `0600`. Tests check that no file next to the database contains stored text.
- No export function, no plaintext cache, no logs with content, no telemetry.

## What ADA does not protect

- Malware running as your user on an unlocked machine
- Memory forensics on a running app. Python cannot reliably wipe the passphrase from memory.
- Someone who watches you type the passphrase
- Access while the app is unlocked: there is no automatic lock
- A forgotten passphrase means lost data, by design

## Untrusted text

Model answers and web search results are treated as untrusted.

- Answers are shown as styled text only. Links, images and HTML are never interpreted, nothing is clickable and nothing is loaded. The model has no tools and cannot start any action.
- Web search results are cut to a fixed size, marked as untrusted data in the prompt, used for one answer, not stored, and never given to the memory function.
- The automatic memory uses **only your own messages**. It adds at most 5 short lines per answer, never edits or deletes existing text, has a size limit and shows what it added. You can edit everything in the Memory dialog or switch the automatic update off in Settings.

## Network

- ADA connects only to the Ollama and SearXNG addresses you configured, plus fixed localhost addresses when you press "Find automatically". There are no redirects, no proxy from the environment and no other connections.
- ADA warns when an address is plain `http://` and not on localhost or a private network.
- With web search on, your message goes to your SearXNG, which forwards it to its search engines.

## Flatpak permissions

`--socket=wayland`, `--share=network`, `--device=dri`. Nothing else: no X11, no filesystem access, no container sockets, no keyring.

## Supply chain

- One third-party Python package: `sqlcipher3` 0.6.5, pinned with a hash in `requirements.txt`.
- Its bundled SQLCipher (4.20.0) is byte for byte identical to the one built from the official source.
- The Flatpak builds that file again from the official SQLCipher tarball and stops if the checksum differs. Tcl is only a build tool and is not part of the app.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting (the repository's **Security** tab → **Report a vulnerability**). Please do not post details in a public issue. This is a hobby project: expect a reply in days, not hours.
