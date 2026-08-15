#!/usr/bin/env python3
"""word-count organ: count whitespace-separated words in input text.

ABI: read one JSON object {"text": "..."} from stdin,
print {"words": N} to stdout, exit 0 on success.
"""
import json
import sys


def main() -> int:
    raw = sys.stdin.read()
    payload = json.loads(raw)
    text = payload.get("text", "")
    words = len(text.split())
    json.dump({"words": words}, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
