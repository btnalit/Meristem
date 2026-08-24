import socket
import threading
import unittest

from substrate.model_gateway_server import _recv_json_line


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


if __name__ == "__main__":
    unittest.main()
