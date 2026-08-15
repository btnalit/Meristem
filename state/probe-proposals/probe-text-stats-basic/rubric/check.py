#!/usr/bin/env python3
"""Executable rubric for probe-text-stats-basic.

Scores the text-stats organ by INVOKING it over its ABI (stdin JSON -> stdout
JSON), never by inspecting its source. Each case is chosen so that a common
sloppy implementation diverges from a correct one:

  - multi_space:        split(" ") counts empty strings -> wrong word count
  - no_trailing_newline: count("\n") gives 0 lines, splitlines() gives 1
  - empty_string:        count("\n")+1 gives 1 line, splitlines() gives 0
  - surrounding_ws:      len(text.strip()) gives wrong char count
  - multiline_no_trail:  count("\n")=2 but splitlines()=3

Receives {"workdir": "...", "probe": "..."} on stdin.
Returns {"score": float, "detail": "..."} on stdout.
"""
import json, subprocess, sys, pathlib


def load_entrypoint(workdir):
    """Read the organ's declared entrypoint from its manifest."""
    manifest_path = (
        pathlib.Path(workdir) / "body" / "organs" / "text-stats" / "organ.json"
    )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return manifest.get("entrypoint", ["python3", "main.py"])
    except (OSError, json.JSONDecodeError):
        return ["python3", "main.py"]


def invoke_organ(workdir, text):
    """Call the text-stats organ over its ABI. Returns parsed output or None."""
    organ_dir = pathlib.Path(workdir) / "body" / "organs" / "text-stats"
    entrypoint = load_entrypoint(workdir)
    try:
        result = subprocess.run(
            entrypoint,
            input=json.dumps({"text": text}),
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(organ_dir),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def check_case(workdir, label, text, exp_words, exp_chars, exp_lines):
    """Run one test case. Returns (passed, detail)."""
    output = invoke_organ(workdir, text)
    if output is None:
        return False, f"{label}: organ failed or returned invalid JSON"
    problems = []
    for field, expected in (
        ("words", exp_words),
        ("chars", exp_chars),
        ("lines", exp_lines),
    ):
        actual = output.get(field)
        if actual != expected:
            problems.append(f"{field}={actual!r} expected {expected!r}")
    if problems:
        return False, f"{label}: {'; '.join(problems)}"
    return True, f"{label}: ok"


def main():
    payload = json.loads(sys.stdin.read() or "{}")
    workdir = str(pathlib.Path(payload.get("workdir") or ".").resolve())

    # (label, input_text, expected_words, expected_chars, expected_lines)
    cases = [
        # Baseline: simple input with trailing newline.
        ("basic", "hello world\n", 2, 12, 1),
        # Discriminating: double space between words.
        #   sloppy split(" ") -> ["hello", "", "world"] = 3 words
        #   correct split()  -> ["hello", "world"] = 2 words
        ("multi_space", "hello  world\n", 2, 13, 1),
        # Discriminating: no trailing newline.
        #   sloppy count("\n")   -> 0 lines
        #   correct splitlines() -> 1 line
        ("no_trailing_newline", "hello world", 2, 11, 1),
        # Discriminating: empty string.
        #   sloppy count("\n")+1 -> 1 line
        #   correct splitlines()  -> 0 lines
        ("empty_string", "", 0, 0, 0),
        # Discriminating: surrounding whitespace.
        #   sloppy len(text.strip()) -> 2 chars
        #   correct len(text)         -> 7 chars
        ("surrounding_ws", "  hi  \n", 1, 7, 1),
        # Discriminating: multiline without trailing newline.
        #   sloppy count("\n")   -> 2 lines
        #   correct splitlines() -> 3 lines
        ("multiline_no_trail", "a\nb\nc", 3, 5, 3),
    ]

    details = []
    passed = 0
    for label, text, ew, ec, el in cases:
        ok, detail = check_case(workdir, label, text, ew, ec, el)
        details.append(detail)
        if ok:
            passed += 1

    score = round(100.0 * passed / len(cases), 1)
    print(json.dumps({"score": score, "detail": "; ".join(details)}))


if __name__ == "__main__":
    main()
