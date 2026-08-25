import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from substrate.model_gateway_server import _recv_json_line

REPO = Path(__file__).resolve().parents[1]


class ModelGatewayServerFramingTests(unittest.TestCase):
    def test_request_split_across_multiple_recv_chunks(self):
        left, right = socket.socketpair()
        try:
            payload = b'{"role":"mutate","prompt":"split"}\n'

            def sender():
                left.sendall(payload[:9])
                left.sendall(payload[9:])
                left.close()

            thread = threading.Thread(target=sender)
            thread.start()
            self.assertEqual(_recv_json_line(right), payload[:-1])
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
        finally:
            right.close()

    def test_incomplete_request_is_rejected(self):
        left, right = socket.socketpair()
        try:
            left.sendall(b'{"role":"mutate"}')
            left.close()
            with self.assertRaises(ValueError):
                _recv_json_line(right)
        finally:
            right.close()


class ServeLoopSurvivesBadConnectionsTests(unittest.TestCase):
    """P0-2: a half-open connection or a client that vanishes before the
    response used to crash the whole `_serve` accept loop (both `recv` and
    the final `sendall` were outside any try/except). One bad connection
    must not take down every connection after it."""

    def test_serve_loop_survives_half_open_and_garbage_connections(self):
        if not hasattr(socket, "AF_UNIX"):
            # AF_UNIX does not exist on Windows Python; the real gateway
            # process only ever runs on the POSIX soil host. This test runs
            # for real in the Linux server verification.
            self.skipTest("AF_UNIX not available on this platform")

        with tempfile.TemporaryDirectory() as tmp:
            socket_path = Path(tmp) / "gateway.sock"
            env = dict(os.environ)
            env.pop("MERISTEM_SOIL_CYCLE", None)
            proc = subprocess.Popen(
                [sys.executable, "-m", "substrate.model_gateway_server",
                 "--socket", str(socket_path)],
                cwd=str(REPO), env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            try:
                deadline = time.monotonic() + 5
                while not socket_path.exists():
                    if proc.poll() is not None or time.monotonic() > deadline:
                        self.fail(f"gateway server did not start: {proc.stderr.read() if proc.stderr else ''}")
                    time.sleep(0.05)

                # 1. connect and close without sending a newline.
                conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                conn.connect(str(socket_path))
                conn.close()

                # 2. connect, send garbage bytes (still no newline), close.
                conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                conn.connect(str(socket_path))
                conn.sendall(b"not a valid request, and no newline either")
                conn.close()

                # 3. the loop must still be alive: one well-formed request
                # gets a well-formed response back.
                conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                conn.connect(str(socket_path))
                conn.sendall(b'{"role":"mutate","prompt":"x"}\n')
                raw = _recv_json_line(conn)
                conn.close()
                response = json.loads(raw.decode("utf-8"))
                self.assertIn(response["status"], ("allowed", "refused", "deferred"))
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)


if __name__ == "__main__":
    unittest.main()
