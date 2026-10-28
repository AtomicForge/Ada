# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for ada/detect.py (throw-away servers on 127.0.0.1)."""
import http.server
import inspect
import json
import socket
import threading
import time
import unittest
from contextlib import contextmanager

from ada import detect


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.requests.append(self.path)
        self.server.behavior(self)

    def log_message(self, *args):
        pass


def reply(h, status=200, body=b"{}"):
    h.send_response(status)
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


def ollama(h):
    reply(h, 200, json.dumps({"models": [{"name": "llama3"}]}).encode())


def searx(h):
    reply(h, 200, json.dumps({"results": []}).encode())


@contextmanager
def serve(behavior):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    server.requests = []
    server.behavior = behavior
    threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", server.requests
    finally:
        server.shutdown()
        server.server_close()


def dead_url():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{sock.getsockname()[1]}"


class FindOllamaTests(unittest.TestCase):
    def test_finds_ollama(self):
        with serve(ollama) as (url, requests):
            self.assertEqual(detect.find_ollama([url]), url)
        self.assertEqual(requests, ["/api/tags"])

    def test_skips_dead_and_wrong_servers(self):
        with serve(lambda h: reply(h, 200, b"<html>")) as (html, _), \
                serve(lambda h: reply(h, 404)) as (nf, _), \
                serve(ollama) as (good, _):
            self.assertEqual(detect.find_ollama([dead_url(), html, nf, good]), good)

    def test_first_match_wins_and_stops(self):
        with serve(ollama) as (a, ra), serve(ollama) as (b, rb):
            self.assertEqual(detect.find_ollama([a, b]), a)
        self.assertEqual(rb, [])

    def test_nothing_found(self):
        self.assertIsNone(detect.find_ollama([dead_url()]))
        self.assertIsNone(detect.find_ollama([]))

    def test_silent_server_is_given_up_quickly(self):
        with serve(lambda h: time.sleep(1.5)) as (url, _):
            started = time.monotonic()
            self.assertIsNone(detect.find_ollama([url], timeout=0.3))
            self.assertLess(time.monotonic() - started, 1.2)

    def test_default_timeout_is_short(self):
        self.assertEqual(detect.PROBE_TIMEOUT, 1.0)
        for func in (detect.find_ollama, detect.find_searxng):
            self.assertEqual(inspect.signature(func).parameters["timeout"].default, 1.0)

    def test_default_lists_are_localhost_only(self):
        for url in detect.OLLAMA_CANDIDATES + detect.SEARXNG_CANDIDATES:
            self.assertTrue(url.startswith("http://localhost:"), url)
        self.assertEqual(detect.OLLAMA_CANDIDATES, ("http://localhost:11434",))
        self.assertEqual(detect.SEARXNG_CANDIDATES,
                         ("http://localhost:8080", "http://localhost:8888"))


class FindSearxngTests(unittest.TestCase):
    def test_finds_searxng(self):
        with serve(searx) as (url, requests):
            self.assertEqual(detect.find_searxng([url]), url)
        self.assertTrue(requests[0].startswith("/search?"))

    def test_403_means_not_usable(self):
        with serve(lambda h: reply(h, 403)) as (url, _):
            self.assertIsNone(detect.find_searxng([url]))

    def test_ollama_is_not_taken_for_searxng(self):
        with serve(ollama) as (url, _):
            self.assertIsNone(detect.find_searxng([url]))

    def test_skips_to_the_working_one(self):
        with serve(searx) as (good, _):
            self.assertEqual(detect.find_searxng([dead_url(), good]), good)

    def test_nothing_found(self):
        self.assertIsNone(detect.find_searxng([dead_url()]))

    def test_silent_server_is_given_up_quickly(self):
        with serve(lambda h: time.sleep(1.5)) as (url, _):
            started = time.monotonic()
            self.assertIsNone(detect.find_searxng([url], timeout=0.3))
            self.assertLess(time.monotonic() - started, 1.2)


if __name__ == "__main__":
    unittest.main()
