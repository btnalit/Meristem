"""Long-lived soil-owned Unix-socket model gateway server."""
from __future__ import annotations

import argparse
import json
import os
import socket
import stat
import signal
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from substrate import model_gateway


def _serve(socket_path: Path) -> int:
    if not socket_path.is_absolute() or socket_path.exists():
        return 2
    socket_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # The supervisor creates this private directory as soil:worker. The
    # server itself remains a pure soil process; the supervisor assigns the
    # worker group to the socket after bind.
    old_umask = os.umask(0o077)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        mode = model_gateway.execution_mode()
        policy = model_gateway.load_execution_policy(mode)
    except (OSError, ValueError) as exc:
        print(f"model_gateway_server: invalid execution mode/policy: {exc!r}",
              file=sys.stderr)
        return 2
    def _shutdown(_signum, _frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    try:
        server.bind(str(socket_path))
        os.chmod(socket_path, 0o660)
        if stat.S_IMODE(socket_path.stat().st_mode) != 0o660:
            return 2
        server.listen(8)
        while True:
            conn, _ = server.accept()
            with conn:
                raw = conn.recv(2 * 1024 * 1024)
                try:
                    request = json.loads(raw.decode("utf-8").strip())
                    cycle = model_gateway._resolve_cycle()
                    if cycle is None:
                        response = {"status": "refused", "reason": "cycle_unknown"}
                    else:
                        response = model_gateway.handle(
                            request, policy=policy,
                            calls_ledger=model_gateway.CALLS_LEDGER_PATH,
                            cycle=cycle)
                except Exception as exc:
                    print(f"model_gateway_server: internal error: {exc!r}", file=sys.stderr)
                    response = {"status": "refused"}
                # §8.1.3: seed observes status only. Provider, retry, policy,
                # and filesystem reasons stay soil-private.
                public = {"status": response.get("status", "refused")}
                if public["status"] == "allowed":
                    public["content"] = response.get("content", "")
                conn.sendall((json.dumps(public) + "\n").encode("utf-8"))
    finally:
        server.close()
        os.umask(old_umask)
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    args = parser.parse_args(argv)
    return _serve(Path(args.socket))


if __name__ == "__main__":
    raise SystemExit(main())
