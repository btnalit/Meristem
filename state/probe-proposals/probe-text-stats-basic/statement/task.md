# Probe: text-stats basic correctness

The text-stats organ accepts a JSON object `{"text": "..."}` on stdin and
returns `{"words": N, "chars": M, "lines": L}` on stdout, where:

- **words** is the number of whitespace-separated tokens (as Python's
  `str.split()` would produce — multiple consecutive spaces do not create
  empty words).
- **chars** is the total number of characters in the input string, including
  whitespace and newlines (i.e. `len(text)`).
- **lines** is the number of lines as `str.splitlines()` would produce — a
  string with no trailing newline still counts as one line, and an empty
  string counts as zero lines.

This probe invokes the organ over its ABI with inputs designed to separate a
correct implementation from common sloppy ones. A probe where both score the
same teaches nothing.
