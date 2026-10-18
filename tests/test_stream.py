# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the chat streaming in ada/ollama.py (throw-away server on 127.0.0.1)."""
import http.client
import http.server
import json
import socket
import threading
import time
import types
import unittest
from contextlib import contextmanager
from unittest import mock

from ada import ollama
from ada.ollama import ChatStream, OllamaError

MESSAGES = [{"role": "user", "content": "Hi"}]


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # like Ollama: chunked answers

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.server.requests.append((self.path, self.rfile.read(length)))
        self.server.behavior(self)

    def log_message(self, *args):  # keep test output quiet
        pass


@contextmanager
def serve(behavior):
    """Run a local test server; yields (base_url, list of (path, body))."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    server.requests = []
    server.behavior = behavior
    # short poll interval, so that shutdown() returns quickly
    threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", server.requests
    finally:
        server.shutdown()
        server.server_close()


def start(handler, chunked=True):
    handler.send_response(200)
    handler.send_header("Content-Type", "application/x-ndjson")
    if chunked:
        handler.send_header("Transfer-Encoding", "chunked")
    else:
        handler.send_header("Connection", "close")  # body ends when we close
    handler.end_headers()
    handler.close_connection = True
    handler.wfile.flush()


def send(handler, data, chunked=True):
    """Send bytes; False if the client has gone away."""
    if chunked:
        data = b"%x\r\n%s\r\n" % (len(data), data)
    try:
        handler.wfile.write(data)
        handler.wfile.flush()
    except OSError:
        return False
    return True


def finish(handler, chunked=True):
    if chunked:
        send(handler, b"0\r\n\r\n", chunked=False)


def reply(handler, status, body, headers=()):
    handler.send_response(status)
    handler.send_header("Content-Length", str(len(body)))
    for key, value in headers:
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(body)


def line(text="", done=False):
    obj = {"model": "m", "message": {"role": "assistant", "content": text}, "done": done}
    return json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n"


def stream_of(url, **kwargs):
    return ChatStream(url, "m", MESSAGES, **kwargs)


def collect(stream):
    """Returns (pieces received, OllamaError or None)."""
    parts = []
    try:
        for piece in stream:
            parts.append(piece)
    except OllamaError as exc:
        return parts, exc
    return parts, None


def run_in_thread(stream):
    state = types.SimpleNamespace(parts=[], error=None, ended=threading.Event())

    def run():
        try:
            for piece in stream:
                state.parts.append(piece)
        except OllamaError as exc:
            state.error = exc
        finally:
            state.ended.set()

    threading.Thread(target=run, daemon=True).start()
    return state


def wait_for(condition, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.02)
    return False


class StreamContentTests(unittest.TestCase):
    def test_pieces_arrive_in_order_and_request_is_right(self):
        for chunked in (True, False):
            with self.subTest(chunked=chunked):
                def behavior(h):
                    start(h, chunked)
                    for text in ("Hel", "lo", " world"):
                        send(h, line(text), chunked)
                    send(h, line("", True), chunked)
                    finish(h, chunked)

                with serve(behavior) as (url, requests):
                    parts, error = collect(stream_of(url))
                self.assertIsNone(error)
                self.assertEqual(parts, ["Hel", "lo", " world"])
                self.assertEqual(len(requests), 1)
                path, body = requests[0]
                self.assertEqual(path, "/api/chat")
                self.assertEqual(
                    json.loads(body),
                    {"model": "m", "messages": MESSAGES, "stream": True},
                )

    def test_path_prefix_is_kept(self):
        def behavior(h):
            start(h)
            send(h, line("", True))
            finish(h)

        with serve(behavior) as (url, requests):
            collect(stream_of(url + "/ollama/"))
        self.assertEqual(requests[0][0], "/ollama/api/chat")

    def test_lines_may_be_split_and_joined_anywhere(self):
        def behavior(h):
            start(h)
            send(h, b'{"message":{"role":"assistant",')
            send(h, b'"content":"A"},"done":false}\n{"message":{"content":"B"},')
            send(h, b'"done":false}\n{"mess')
            # the last line has no newline at the end
            send(h, b'age":{"content":"C"},"done":true}')
            finish(h)

        with serve(behavior) as (url, _):
            parts, error = collect(stream_of(url))
        self.assertIsNone(error)
        self.assertEqual(parts, ["A", "B", "C"])

    def test_multibyte_character_split_between_chunks(self):
        data = line("aü🙂b", True)
        cut = data.index("ü".encode("utf-8")) + 1  # in the middle of ü

        def behavior(h):
            start(h)
            send(h, data[:cut])
            time.sleep(0.05)
            send(h, data[cut:])
            finish(h)

        with serve(behavior) as (url, _):
            parts, error = collect(stream_of(url))
        self.assertIsNone(error)
        self.assertEqual(parts, ["aü🙂b"])

    def test_unimportant_lines_are_ignored(self):
        def behavior(h):
            start(h)
            for data in (b"\n", line(""), b'{"done": false}\n', b'{"message": "x"}\n',
                         b'{"message": {"content": 5}}\n', b"   \n", line("ok")):
                send(h, data)
            send(h, line("", True))
            finish(h)

        with serve(behavior) as (url, _):
            parts, error = collect(stream_of(url))
        self.assertIsNone(error)
        self.assertEqual(parts, ["ok"])

    def test_nothing_is_read_after_done(self):
        def behavior(h):
            start(h)
            send(h, line("Hi") + line("", True) + b"garbage\n")
            finish(h)

        with serve(behavior) as (url, _):
            parts, error = collect(stream_of(url))
        self.assertIsNone(error)
        self.assertEqual(parts, ["Hi"])

    def test_only_role_and_content_are_sent(self):
        messages = [{"role": "user", "content": "Hi", "images": ["AAA"], "x": 1}]

        def behavior(h):
            start(h)
            send(h, line("", True))
            finish(h)

        with serve(behavior) as (url, requests):
            collect(ChatStream(url, "m", messages))
        self.assertEqual(json.loads(requests[0][1])["messages"], MESSAGES)


class StreamErrorTests(unittest.TestCase):
    def test_broken_lines_raise_after_the_good_text(self):
        broken = [b"not json\n", b"[1, 2]\n", b'"text"\n', b"\xff\xfe\n",
                  b'{"message": {"content": "x"}\n', b"[" * 100000 + b"\n"]
        for bad in broken:
            with self.subTest(bad=bad[:20]):
                def behavior(h, bad=bad):
                    start(h)
                    send(h, line("Hi"))
                    send(h, bad)
                    finish(h)

                with serve(behavior) as (url, _):
                    parts, error = collect(stream_of(url))
                self.assertEqual(parts, ["Hi"])
                self.assertIsInstance(error, OllamaError)

    def test_error_line_in_the_stream(self):
        def behavior(h):
            start(h)
            send(h, line("Hi"))
            send(h, b'{"error": "boom"}\n')
            finish(h)

        with serve(behavior) as (url, _):
            parts, error = collect(stream_of(url))
        self.assertEqual(parts, ["Hi"])
        self.assertIn("boom", str(error))

    def test_error_text_from_the_server_is_cleaned(self):
        def behavior(h):
            start(h)
            send(h, json.dumps({"error": "\x1b[31mred\n" + "x" * 1000}).encode() + b"\n")
            finish(h)

        with serve(behavior) as (url, _):
            _, error = collect(stream_of(url))
        text = str(error)
        self.assertIn("red", text)
        self.assertTrue(text.isprintable())
        self.assertLess(len(text), 300)

    def test_http_error_with_message(self):
        body = json.dumps({"error": "model 'x' not found"}).encode()
        with serve(lambda h: reply(h, 404, body)) as (url, _):
            parts, error = collect(stream_of(url))
        self.assertEqual(parts, [])
        self.assertIn("not found", str(error))

    def test_http_error_without_message(self):
        with serve(lambda h: reply(h, 500, b"<html>oops</html>")) as (url, _):
            _, error = collect(stream_of(url))
        self.assertIn("500", str(error))

    def test_redirect_is_not_followed(self):
        with serve(lambda h: reply(h, 307, b"", [("Location", "/elsewhere")])) as (
            url, requests,
        ):
            _, error = collect(stream_of(url))
        self.assertIsInstance(error, OllamaError)
        self.assertEqual([path for path, _ in requests], ["/api/chat"])

    def test_cut_off_stream_is_reported(self):
        for chunked in (True, False):
            with self.subTest(chunked=chunked):
                def behavior(h):
                    start(h, chunked)
                    send(h, line("Hi"), chunked)  # then the connection just ends

                with serve(behavior) as (url, _):
                    parts, error = collect(stream_of(url))
                self.assertEqual(parts, ["Hi"])
                self.assertIsInstance(error, OllamaError)

    def test_oversized_line_is_refused(self):
        def behavior(h):
            start(h)
            send(h, b"x" * 500)  # no newline, and the server then stays silent
            time.sleep(1.5)

        with serve(behavior) as (url, _):
            with mock.patch.object(ollama, "MAX_LINE_BYTES", 100):
                started = time.monotonic()
                _, error = collect(stream_of(url, idle_timeout=1.0))
                elapsed = time.monotonic() - started
        # refused at once as invalid, not only after the idle timeout
        self.assertEqual(str(error), ollama._MSG_INVALID)
        self.assertLess(elapsed, 0.8)

    def test_oversized_stream_is_refused(self):
        def behavior(h):
            start(h)
            for _ in range(20):
                if not send(h, line("x" * 50)):
                    break
            time.sleep(0.2)

        with serve(behavior) as (url, _):
            with mock.patch.object(ollama, "MAX_STREAM_BYTES", 200):
                parts, error = collect(stream_of(url))
        self.assertIsInstance(error, OllamaError)
        self.assertLess(len(parts), 20)

    def test_silence_after_a_piece_times_out(self):
        def behavior(h):
            start(h)
            send(h, line("Hi"))
            time.sleep(2)

        with serve(behavior) as (url, _):
            started = time.monotonic()
            parts, error = collect(stream_of(url, idle_timeout=0.3))
            elapsed = time.monotonic() - started
        self.assertEqual(parts, ["Hi"])
        self.assertIn("Zeitüberschreitung", str(error))
        self.assertLess(elapsed, 1.5)

    def test_silence_before_the_answer_times_out(self):
        with serve(lambda h: time.sleep(2)) as (url, _):
            started = time.monotonic()
            parts, error = collect(stream_of(url, idle_timeout=0.3))
            elapsed = time.monotonic() - started
        self.assertEqual(parts, [])
        self.assertIsInstance(error, OllamaError)
        self.assertLess(elapsed, 1.5)

    def test_connection_refused(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        # the socket is closed now, so nobody listens on this port
        _, error = collect(stream_of(f"http://127.0.0.1:{port}", connect_timeout=2))
        self.assertIn("nicht erreichbar", str(error))

    def test_bad_input_is_rejected_at_once(self):
        good = "http://localhost:11434"
        bad = [
            ("ftp://localhost", "m", MESSAGES),
            ("http://user:pw@host", "m", MESSAGES),
            (good, "", MESSAGES), (good, None, MESSAGES), (good, "bad\nname", MESSAGES),
            (good, "x" * 201, MESSAGES),
            (good, "m", []), (good, "m", None), (good, "m", "text"), (good, "m", ["text"]),
            (good, "m", [{"role": "tool", "content": "x"}]),
            (good, "m", [{"content": "x"}]),
            (good, "m", [{"role": "user"}]),
            (good, "m", [{"role": "user", "content": 5}]),
        ]
        for url, model, messages in bad:
            with self.subTest(url=url, model=model, messages=messages):
                with self.assertRaises(OllamaError):
                    ChatStream(url, model, messages)


class StreamCancelTests(unittest.TestCase):
    def test_cancel_stops_a_running_stream_quickly(self):
        for chunked in (True, False):
            with self.subTest(chunked=chunked):
                def behavior(h):
                    start(h, chunked)
                    for _ in range(100):
                        if not send(h, line("x"), chunked):
                            return
                        time.sleep(0.05)

                with serve(behavior) as (url, _):
                    stream = stream_of(url)
                    state = run_in_thread(stream)
                    self.assertTrue(wait_for(lambda: state.parts))
                    stream.cancel()
                    self.assertTrue(state.ended.wait(1.0), "stream did not stop")
                self.assertIsNone(state.error)
                self.assertGreaterEqual(len(state.parts), 1)

    def test_cancel_while_waiting_for_the_first_word(self):
        with serve(lambda h: time.sleep(3)) as (url, requests):
            stream = stream_of(url)
            state = run_in_thread(stream)
            self.assertTrue(wait_for(lambda: requests))  # the request has arrived
            stream.cancel()
            self.assertTrue(state.ended.wait(1.5), "stream did not stop")
        self.assertIsNone(state.error)
        self.assertEqual(state.parts, [])

    def test_cancel_before_start_does_not_even_connect(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            stream = stream_of(f"http://127.0.0.1:{listener.getsockname()[1]}")
            stream.cancel()
            self.assertEqual(collect(stream), ([], None))
            listener.settimeout(0.3)
            with self.assertRaises(TimeoutError):
                listener.accept()  # nobody connected

    def test_cancel_during_connect_sends_no_request(self):
        # cancel() can come while connect() is still running, when there is no
        # socket yet to shut down. The stream must notice that afterwards.
        real_connect = http.client.HTTPConnection.connect

        def slow_connect(conn):
            real_connect(conn)
            stream._cancelled.set()  # what cancel() does first

        with serve(lambda h: time.sleep(1)) as (url, requests):
            stream = stream_of(url)
            with mock.patch.object(http.client.HTTPConnection, "connect", slow_connect):
                started = time.monotonic()
                self.assertEqual(collect(stream), ([], None))
            time.sleep(0.2)
            self.assertEqual(requests, [])
            self.assertLess(time.monotonic() - started, 0.8)

    def test_cancel_twice_and_after_the_end_is_harmless(self):
        def behavior(h):
            start(h)
            send(h, line("Hi", True))
            finish(h)

        with serve(behavior) as (url, _):
            stream = stream_of(url)
            parts, error = collect(stream)
            stream.cancel()
            stream.cancel()
        self.assertEqual((parts, error), (["Hi"], None))

    def test_closing_the_iteration_early_is_safe(self):
        def behavior(h):
            start(h)
            for _ in range(100):
                if not send(h, line("x")):
                    return
                time.sleep(0.02)

        with serve(behavior) as (url, _):
            pieces = iter(stream_of(url))
            self.assertEqual(next(pieces), "x")
            pieces.close()


if __name__ == "__main__":
    unittest.main()
