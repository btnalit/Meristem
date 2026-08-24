"""Unprivileged seed-side client for the soil model gateway.

The client knows only a Unix socket path. It never receives or opens the
provider credential file; the soil-owned server is the only credential reader.
"""
from __future__ import annotations

import json
import os
import socket
import sys

_TIMEOUT_SECONDS = 4000


def request(payload: dict) -> dict:
    path = os.environ.get("MERISTEM_MODEL_SOCKET", "")
    if not path or not os.path.isabs(path):
        return {"status": "refused", "reason": "gateway_not_injected"}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(_TIMEOUT_SECONDS)
            client.connect(path)
            client.sendall((json.dumps(payload, separators=(",", ":")) + "\n").encode())
            chunks = []
            while b"\n" not in b"".join(chunks):
                chunk = client.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                if sum(map(len, chunks)) > 2 * 1024 * 1024:
                    return {"status": "refused", "reason": "gateway_bad_response"}
        line = b"".join(chunks).splitlines()[0]
        result = json.loads(line.decode("utf-8"))
    except (OSError, ValueError, IndexError, UnicodeError):
        return {"status": "refused", "reason": "gateway_unavailable"}
    if not isinstance(result, dict) or result.get("status") not in {"allowed", "refused", "deferred"}:
        return {"status": "refused", "reason": "gateway_bad_response"}
    public = {"status": result["status"]}
    if public["status"] == "allowed":
        public["content"] = result.get("content", "")
    return public


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        print(json.dumps({"status": "refused", "reason": "bad_request"}))
        return 0
    print(json.dumps(request(payload)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
