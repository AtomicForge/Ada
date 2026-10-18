# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for ada/ollama.py. They use only a throw-away server on 127.0.0.1."""
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time
import unittest
from contextlib import contextmanager
from unittest import mock

from ada import ollama
from ada.ollama import OllamaError, list_models, needs_plaintext_warning
from ada.ollama import parse_models, validate_url

GOOD_BODY = json.dumps(
    {"models": [{"name": "llama3:8b"}, {"name": "mistral:latest"}]}
).encode()


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.requests.append(self.path)
        self.server.behavior(self)

    def log_message(self, *args):  # keep test output quiet
        pass


def reply(handler, status=200, body=GOOD_BODY, headers=()):
    handler.send_response(status)
    for key, value in headers:
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(body)


@contextmanager
def serve(behavior):
    """Run a local test server; yields (base_url, list of requested paths)."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    server.requests = []
    server.behavior = behavior
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", server.requests
    finally:
        server.shutdown()
        server.server_close()


class ValidateUrlTests(unittest.TestCase):
    def test_valid_urls_are_normalized(self):
        cases = {
            "http://localhost:11434": "http://localhost:11434",
            "http://localhost:11434/": "http://localhost:11434",
            "  http://localhost:11434  ": "http://localhost:11434",
            "HTTP://LocalHost:11434": "http://localhost:11434",
            "https://ollama.example.com": "https://ollama.example.com",
            "http://192.168.1.5:11434": "http://192.168.1.5:11434",
            "http://[::1]:11434/": "http://[::1]:11434",
            "https://example.com/ollama/": "https://example.com/ollama",
        }
        for given, expected in cases.items():
            with self.subTest(given=given):
                self.assertEqual(validate_url(given), expected)

    def test_default_url_is_valid(self):
        self.assertEqual(validate_url(ollama.DEFAULT_URL), ollama.DEFAULT_URL)

    def test_invalid_urls_are_rejected(self):
        bad = [
            None, 42, "", "   ", "localhost:11434", "ftp://host", "file:///etc/passwd",
            "javascript:alert(1)", "http://", "http://:11434", "http:///path",
            "http://user:pw@host", "http://user@host", "http://host:99999",
            "http://host:abc", "http://host?x=1", "http://host#frag",
            "http://ho st", "http://host\n/", "http://host\t/", "http://host\x00/",
        ]
        for given in bad:
            with self.subTest(given=given):
                with self.assertRaises(OllamaError):
                    validate_url(given)


class PlaintextWarningTests(unittest.TestCase):
    def test_trusted_addresses_give_no_warning(self):
        trusted = [
            "http://localhost:11434", "http://127.0.0.1:11434", "http://127.5.5.5",
            "http://[::1]:11434", "http://10.1.2.3", "http://172.16.0.1",
            "http://172.31.255.255", "http://192.168.1.5:11434",
            "http://100.64.0.1", "http://100.127.255.255",
            "http://[fd7a:115c:a1e0::1]",
        ]
        for url in trusted:
            with self.subTest(url=url):
                self.assertFalse(needs_plaintext_warning(url))

    def test_other_http_addresses_give_warning(self):
        untrusted = [
            "http://172.15.0.1", "http://172.32.0.1", "http://100.63.255.255",
            "http://100.128.0.1", "http://8.8.8.8", "http://192.169.0.1",
            "http://11.0.0.1", "http://[2001:db8::1]",
            # host names cannot be checked without DNS, so they warn
            "http://homeserver", "http://ollama.example.com",
            "http://localhost.evil.com", "http://10.0.0.1.evil.com",
            "http://127.1",
        ]
        for url in untrusted:
            with self.subTest(url=url):
                self.assertTrue(needs_plaintext_warning(url))

    def test_https_never_warns(self):
        for url in ("https://8.8.8.8", "https://ollama.example.com"):
            with self.subTest(url=url):
                self.assertFalse(needs_plaintext_warning(url))

    def test_invalid_url_raises(self):
        with self.assertRaises(OllamaError):
            needs_plaintext_warning("ftp://host")


class ParseModelsTests(unittest.TestCase):
    def test_names_are_sorted_and_deduplicated(self):
        raw = json.dumps(
            {"models": [{"name": "b"}, {"name": "a"}, {"name": "b"}]}
        ).encode()
        self.assertEqual(parse_models(raw), ["a", "b"])

    def test_empty_list_is_valid(self):
        self.assertEqual(parse_models(b'{"models": []}'), [])

    def test_unusable_entries_are_skipped(self):
        raw = json.dumps(
            {
                "models": [
                    "text", 5, None, {}, {"name": 5}, {"name": ""},
                    {"name": "bad\nname"}, {"name": "x" * 201},
                    {"name": "good:1b"},
                ]
            }
        ).encode()
        self.assertEqual(parse_models(raw), ["good:1b"])

    def test_broken_answers_raise(self):
        bad = [
            b"", b"not json", b'{"models": [', b"\xff\xfe", b"[]", b"null",
            b'"text"', b"{}", b'{"models": "x"}', b'{"models": {"name": "a"}}',
            b"[" * 100000,  # nesting so deep that json raises RecursionError
        ]
        for raw in bad:
            with self.subTest(raw=raw[:20]):
                with self.assertRaises(OllamaError):
                    parse_models(raw)


class ListModelsTests(unittest.TestCase):
    def test_success_asks_the_right_path(self):
        with serve(reply) as (url, requests):
            self.assertEqual(list_models(url), ["llama3:8b", "mistral:latest"])
        self.assertEqual(requests, ["/api/tags"])

    def test_path_prefix_is_kept(self):
        with serve(reply) as (url, requests):
            list_models(url + "/ollama/")
        self.assertEqual(requests, ["/ollama/api/tags"])

    def test_invalid_url_is_rejected_before_any_request(self):
        with self.assertRaises(OllamaError):
            list_models("ftp://localhost")

    def test_http_error_status(self):
        with serve(lambda h: reply(h, 500, b"boom")) as (url, _):
            with self.assertRaises(OllamaError):
                list_models(url)

    def test_invalid_json_answer(self):
        with serve(lambda h: reply(h, 200, b"<html>hi</html>")) as (url, _):
            with self.assertRaises(OllamaError):
                list_models(url)

    def test_redirect_is_not_followed(self):
        def behavior(handler):
            if handler.path == "/api/tags":
                reply(handler, 302, b"", [("Location", "/elsewhere")])
            else:
                reply(handler)

        with serve(behavior) as (url, requests):
            with self.assertRaises(OllamaError):
                list_models(url)
        self.assertEqual(requests, ["/api/tags"])

    def test_oversized_answer_is_refused(self):
        with serve(reply) as (url, _):
            with mock.patch.object(ollama, "MAX_RESPONSE_BYTES", 10):
                with self.assertRaises(OllamaError):
                    list_models(url)

    def test_connection_refused(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        # the socket is closed now, so nobody listens on this port
        with self.assertRaises(OllamaError):
            list_models(f"http://127.0.0.1:{port}", timeout=2)

    def test_silent_server_times_out(self):
        def behavior(handler):
            time.sleep(1.5)

        with serve(behavior) as (url, _):
            started = time.monotonic()
            with self.assertRaises(OllamaError):
                list_models(url, timeout=0.3)
            self.assertLess(time.monotonic() - started, 1.2)

    def test_slowly_dripping_server_hits_total_deadline(self):
        # Sends a byte every 0.1 s: each single read is fast, but the whole
        # answer never ends. The total deadline must stop it.
        def behavior(handler):
            handler.send_response(200)
            handler.end_headers()
            try:
                for _ in range(50):
                    handler.wfile.write(b" ")
                    handler.wfile.flush()
                    time.sleep(0.1)
            except OSError:
                pass

        with serve(behavior) as (url, _):
            started = time.monotonic()
            with self.assertRaises(OllamaError):
                list_models(url, timeout=0.5)
            self.assertLess(time.monotonic() - started, 2.0)

    def test_environment_proxy_is_ignored(self):
        # urllib reads proxy settings when it is imported, so a fresh process is
        # needed to test this for real.
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        proxy = "http://127.0.0.1:9"  # nobody listens there
        env = dict(
            os.environ, http_proxy=proxy, HTTP_PROXY=proxy, all_proxy=proxy,
            ALL_PROXY=proxy, no_proxy="", NO_PROXY="", PYTHONPATH=root,
        )
        code = "import sys; from ada.ollama import list_models; print(list_models(sys.argv[1]))"
        with serve(reply) as (url, requests):
            result = subprocess.run(
                [sys.executable, "-c", code, url],
                env=env, capture_output=True, text=True, timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(requests, ["/api/tags"])


if __name__ == "__main__":
    unittest.main()
