#!/usr/bin/env python3
"""text-stats organ.

Reads {"text": "..."} from stdin, returns {"words": N, "chars": M,
"lines": L} on stdout.

Depends on word-count (declared in organ.json). The dependency is
structural: text-stats extends word-count's capability domain, so
word-count's files belong in text-stats's review closure. Word
counting is implemented directly here rather than via a subprocess
call to word-count — that would introduce an undeclared effect:run
edge. Keeping the implementation self-contained means the only
dependency edge is the declared one, which is exactly what the
closure calculator expects to see.
"""
from __future__ import annotations

import json
import sys


def main() -> None:
    payload = json.loads(sys.stdin.read() or "{}")
    text = payload.get("text", "")
    words = len(text.split())
    chars = len(text)
    lines = len(text.splitlines())
    print(json.dumps({"words": words, "chars": chars, "lines": lines}))


if __name__ == "__main__":
    main()
