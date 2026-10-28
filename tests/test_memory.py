# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the memory proposals in ada/ollama.py (throw-away server on 127.0.0.1)."""
import http.server
import json
import threading
import time
import unittest
from contextlib import contextmanager
from unittest import mock

from ada import ollama
from ada.ollama import OllamaError, parse_facts, propose_memory


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.server.requests.append((self.path, self.rfile.read(length)))
        self.server.behavior(self)

    def log_message(self, *args):  # keep test output quiet
        pass


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


def say(*pieces):
    """A server that streams the given pieces of text as the answer."""
    def behavior(h):
        h.send_response(200)
        h.send_header("Transfer-Encoding", "chunked")
        h.end_headers()
        h.close_connection = True
        for text, done in [(p, False) for p in pieces] + [("", True)]:
            obj = {"message": {"role": "assistant", "content": text}, "done": done}
            data = json.dumps(obj).encode() + b"\n"
            h.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
            h.wfile.flush()
        h.wfile.write(b"0\r\n\r\n")
    return behavior


class ParseFactsTests(unittest.TestCase):
    def test_lines_with_dash_are_taken(self):
        self.assertEqual(parse_facts("- Mag Podman\n- Nutzt Bazzite\n"),
                         ["Mag Podman", "Nutzt Bazzite"])

    def test_everything_else_is_ignored(self):
        text = ("KEINE\nHier sind Fakten:\n* Stern\n• Punkt\n  - eingerückt\n-ohne Leerzeichen\n"
                "1. Nummer\n- \n-  \n- gut\nSchluss")
        self.assertEqual(parse_facts(text), ["gut"])

    def test_at_most_five(self):
        self.assertEqual(parse_facts("\n".join(f"- f{i}" for i in range(20))),
                         [f"f{i}" for i in range(5)])

    def test_too_long_lines_are_skipped_and_do_not_count(self):
        text = "- " + "x" * 201 + "\n- " + "y" * 200 + "\n- ok"
        self.assertEqual(parse_facts(text), ["y" * 200, "ok"])

    def test_unprintable_lines_are_skipped(self):
        self.assertEqual(parse_facts("- a\x1b[31mb\n- tab\there\n- ok"), ["ok"])

    def test_duplicates_and_spaces(self):
        self.assertEqual(parse_facts("-   a  \n- a\n- b"), ["a", "b"])

    def test_empty_and_odd_answers(self):
        for text in ("", "\n\n", "KEINE", "-", "- "):
            with self.subTest(text=text):
                self.assertEqual(parse_facts(text), [])


class ProposeMemoryTests(unittest.TestCase):
    def test_request_contains_only_the_owners_text(self):
        with serve(say("- Mag Podman")) as (url, requests):
            facts = propose_memory(url, "m", ["Ich nutze USERMARK-A", "Und USERMARK-B"])
        self.assertEqual(facts, ["Mag Podman"])
        path, body = requests[0]
        self.assertEqual(path, "/api/chat")
        data = json.loads(body)
        self.assertTrue(data["stream"])
        self.assertEqual([m["role"] for m in data["messages"]], ["system", "user"])
        self.assertEqual(data["messages"][0]["content"], ollama._PROPOSAL_PROMPT)
        self.assertEqual(data["messages"][1]["content"],
                         "Ich nutze USERMARK-A\n\nUnd USERMARK-B")

    def test_answer_is_parsed_from_several_pieces(self):
        with serve(say("- Mag ", "Podman\n- Zwe", "ites\nPlauderei")) as (url, _):
            self.assertEqual(propose_memory(url, "m", ["x"]), ["Mag Podman", "Zweites"])

    def test_nothing_to_remember(self):
        with serve(say("KEINE")) as (url, _):
            self.assertEqual(propose_memory(url, "m", ["Hallo"]), [])

    def test_at_most_five_facts(self):
        with serve(say("\n".join(f"- f{i}" for i in range(9)))) as (url, _):
            self.assertEqual(len(propose_memory(url, "m", ["x"])), 5)

    def test_empty_input_sends_no_request(self):
        with serve(say("- x")) as (url, requests):
            for texts in ([], [""], ["  \n "], ["", " "]):
                with self.subTest(texts=texts):
                    self.assertEqual(propose_memory(url, "m", texts), [])
        self.assertEqual(requests, [])

    def test_bad_input_is_rejected(self):
        for texts in (None, "text", 5, [5], ["a", None], [["a"]], ("a",)):
            with self.subTest(texts=texts):
                with self.assertRaises(OllamaError):
                    propose_memory("http://127.0.0.1:1", "m", texts)

    def test_long_input_is_cut(self):
        with serve(say("- x")) as (url, requests):
            propose_memory(url, "m", ["a" * 10000])
        sent = json.loads(requests[0][1])["messages"][1]["content"]
        self.assertEqual(len(sent), ollama.MAX_PROPOSAL_INPUT)

    def test_endless_answer_is_cut_and_stops_quickly(self):
        def behavior(h):
            h.send_response(200)
            h.send_header("Transfer-Encoding", "chunked")
            h.end_headers()
            h.close_connection = True
            for i in range(1000):
                text = f"- fact {i}\n" if i < 3 else "- cut-" + "x" * 50 + "\n"
                data = json.dumps({"message": {"content": text}, "done": False}).encode() + b"\n"
                try:
                    h.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
                    h.wfile.flush()
                except OSError:
                    return
                time.sleep(0.01)

        with serve(behavior) as (url, _):
            with mock.patch.object(ollama, "MAX_PROPOSAL_ANSWER", 100):
                started = time.monotonic()
                facts = propose_memory(url, "m", ["x"])
                elapsed = time.monotonic() - started
        self.assertLess(elapsed, 3.0)
        self.assertEqual(facts[:3], ["fact 0", "fact 1", "fact 2"])
        self.assertLessEqual(len(facts), 5)

    def test_server_errors_raise(self):
        def behavior(h):
            body = b'{"error": "model not found"}'
            h.send_response(404)
            h.send_header("Content-Length", str(len(body)))
            h.end_headers()
            h.wfile.write(body)

        with serve(behavior) as (url, _):
            with self.assertRaises(OllamaError) as cm:
                propose_memory(url, "m", ["x"])
        self.assertIn("not found", str(cm.exception))

    def test_cut_off_answer_raises(self):
        def behavior(h):
            h.send_response(200)
            h.send_header("Connection", "close")
            h.end_headers()
            h.close_connection = True
            h.wfile.write(b'{"message": {"content": "- Teil"}, "done": false}\n')

        with serve(behavior) as (url, _):
            with self.assertRaises(OllamaError):
                propose_memory(url, "m", ["x"])


if __name__ == "__main__":
    unittest.main()
