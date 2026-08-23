"""Shared helpers for the Meristem v5 CI consistency assertions (SA-1..5, CA-1..12).

See tests/ci/README.md for what each assertion is and how to run them.

This package intentionally has NO third-party dependencies (stdlib only), per the
P0-a dispatch constraint ("不装依赖、只用标准库").
"""
from __future__ import annotations

import re
import shlex
import stat
from pathlib import Path

# ---------------------------------------------------------------------------
# Repository layout
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = REPO_ROOT / "docs" / "MERISTEM-V5-SPEC.md"
MATRIX_PATH = REPO_ROOT / "soil" / "authority-matrix.json"
LEDGER_PATH = REPO_ROOT / "state" / "soil-ledger.jsonl"
SCOREBOARD_PATH = REPO_ROOT / "state" / "soil-scoreboard.jsonl"
REPORT_FACTS_PATH = REPO_ROOT / "soil" / "report-facts.json"
SEED_FEEDBACK_PATH = REPO_ROOT / "seed" / "feedback.json"
FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
LEDGER_ALL_KINDS_FIXTURE = FIXTURES_DIR / "soil-ledger-all-kinds.jsonl"


def read_spec_text() -> str:
    return SPEC_PATH.read_text(encoding="utf-8")


def read_spec_lines() -> list[str]:
    # keepends=False; index i corresponds to 1-based line number i+1
    return read_spec_text().splitlines()


# ---------------------------------------------------------------------------
# §16 authority-matrix Markdown table parsing (for SA-2 / SA-3)
# ---------------------------------------------------------------------------

MATRIX_COLUMNS = [
    "seed_read", "seed_write", "soil_read", "soil_write",
    "soil_execute", "root_write", "in_report",
]

_SEPARATOR_ROW_RE = re.compile(r"^\|[\s:\-|]+\|$")


def _normalize_cell(text: str) -> str:
    """Strip Markdown emphasis (**bold**, `code`) and surrounding whitespace.

    Bold/backtick markup in §16 is presentation only -- it never changes which
    of the 7 permission values a cell holds. SA-2 compares *content*, not
    Markdown decoration, so both the parsed table cells and the JSON
    transcription are normalized through this same function before comparison.
    """
    t = text.strip()
    t = t.replace("**", "")
    t = t.replace("`", "")
    return t.strip()


def parse_authority_matrix_table(spec_text: str | None = None) -> list[dict]:
    """Parse the §16 Markdown table into a list of row dicts:
    {"name": str, "seed_read": str, ..., "in_report": str}

    Locates the table between the "## 16." heading and the next "## 17."
    heading, so it stays correct if unrelated sections shift line numbers.
    """
    text = spec_text if spec_text is not None else read_spec_text()
    lines = text.splitlines()

    start = None
    end = len(lines)
    for i, line in enumerate(lines):
        if line.strip().startswith("## 16."):
            start = i
        elif start is not None and line.strip().startswith("## 17."):
            end = i
            break
    if start is None:
        raise AssertionError("could not locate '## 16.' heading in spec")

    table_lines = [
        line for line in lines[start:end]
        if line.strip().startswith("|") and line.strip().endswith("|")
    ]
    if len(table_lines) < 3:
        raise AssertionError(
            f"§16 table has only {len(table_lines)} pipe-rows; expected header + separator + data"
        )

    # First pipe-row = header, second = separator; rest = data.
    data_lines = table_lines[2:]

    rows = []
    for line in data_lines:
        cells = [c for c in line.strip().split("|")]
        # A well-formed "| a | b | c |" row splits into ['', ' a ', ' b ', ' c ', '']
        assert cells[0] == "" and cells[-1] == "", (
            f"malformed table row (must start/end with '|'): {line!r}"
        )
        cells = cells[1:-1]
        if len(cells) != 1 + len(MATRIX_COLUMNS):
            raise AssertionError(
                f"expected {1 + len(MATRIX_COLUMNS)} cells, got {len(cells)}: {line!r}"
            )
        normalized = [_normalize_cell(c) for c in cells]
        row = {"name": normalized[0]}
        for col, val in zip(MATRIX_COLUMNS, normalized[1:]):
            row[col] = val
        rows.append(row)
    return rows


def load_authority_matrix_json() -> dict:
    import json
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# §17.7 scan-word-list extraction (for SA-4)
# ---------------------------------------------------------------------------

def extract_scan_word_lists(spec_text: str | None = None) -> tuple[list[str], list[str], tuple[int, int]]:
    """Extract list ① (current/live names) and list ② (retired names) from the
    §17.7 bash scan block, plus the (start_line, end_line) 0-based span of that
    fenced code block (so callers can exclude it from "usage" counts).

    Returns (current_words, retired_words, (block_start_idx, block_end_idx)).
    """
    text = spec_text if spec_text is not None else read_spec_text()
    lines = text.splitlines()

    heading_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith("## 17.7"):
            heading_idx = i
            break
    if heading_idx is None:
        raise AssertionError("could not locate '## 17.7' heading in spec")

    section_end = len(lines)
    for i in range(heading_idx + 1, len(lines)):
        if lines[i].startswith("## "):
            section_end = i
            break

    fence_start = None
    fence_end = None
    for i in range(heading_idx, section_end):
        if lines[i].strip().startswith("```"):
            if fence_start is None:
                fence_start = i
            else:
                fence_end = i
                break
    if fence_start is None or fence_end is None:
        raise AssertionError("could not locate fenced code block in §17.7")

    block_text = "\n".join(lines[fence_start:fence_end + 1])

    matches = re.findall(r"for w in (.*?); do", block_text, re.DOTALL)
    if len(matches) != 2:
        raise AssertionError(
            f"expected exactly 2 'for w in ...; do' loops in §17.7, found {len(matches)}"
        )

    def _tokenize(raw: str) -> list[str]:
        raw = raw.replace("\\\n", " ")
        return shlex.split(raw)

    current_words = _tokenize(matches[0])
    retired_words = _tokenize(matches[1])
    return current_words, retired_words, (fence_start, fence_end)


def blockquote_paragraphs_with_marker(lines: list[str], marker: str) -> set[int]:
    """0-based line indices belonging to a contiguous Markdown blockquote
    paragraph (consecutive lines whose stripped form starts with '>') that
    contains `marker` somewhere in that same paragraph.

    Used by SA-4 to honor §17.7's stated "显式的「本轮勘误」注记" exemption: the
    annotation conventionally opens a multi-line blockquote remark (e.g.
    "> **本轮勘误**: ..." followed by continuation lines), and the retired
    term being explained often lands on a *later* line of that same
    paragraph, not the annotated line itself. A pure single-line marker
    check (as used for §18) would miss those; this groups by contiguous '>'
    runs instead, which is a bounded, literal reading of "same remark", not
    an open-ended fuzzy window.
    """
    exempted: set[int] = set()
    i = 0
    n = len(lines)
    while i < n:
        if lines[i].lstrip().startswith(">"):
            para = []
            j = i
            while j < n and lines[j].lstrip().startswith(">"):
                para.append(j)
                j += 1
            if any(marker in lines[k] for k in para):
                exempted.update(para)
            i = j
        else:
            i += 1
    return exempted


def find_section_span(spec_text: str, heading_prefix: str) -> tuple[int, int]:
    """0-based [start, end) line span of the top-level section whose heading
    startswith heading_prefix (e.g. '## 18.')."""
    lines = spec_text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip().startswith(heading_prefix):
            start = i
            break
    if start is None:
        raise AssertionError(f"could not locate heading {heading_prefix!r}")
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break
    return start, end


# ---------------------------------------------------------------------------
# §8.2 declared soil-ledger `kind` values (for CA-6a / CA-6b)
# ---------------------------------------------------------------------------
# Sourced from docs/MERISTEM-V5-SPEC.md:
#   - "kind": "observed_fitness"     §8.2 event envelope example (line ~558),
#                                     written in substrate/pipeline.py (§10.2, line ~860)
#   - "kind": "promotion_intent"     §10.2 pipeline.py (line ~898)
#   - "kind": "accepted_fitness"     §1.2 is_ignition_event predicate; §10.2 (line ~902)
#   - "kind": "promotion_committed"  §10.2 pipeline.py (line ~911)
#   - "kind": "promotion_outcome"    §10.2 finalize_nonpromotion() -- the SOLE
#                                     non-promotion exit (§10.3 failure-path table);
#                                     STALE/REGRESSED/UNFULFILLED/REJECTED/
#                                     CANARY_REJECT/UNMEASURED all live in its
#                                     `outcome` field, not as separate `kind`s
#                                     (§10, "本轮勘误" erratum: kind column was
#                                     wrong in an earlier draft, fixed to
#                                     `promotion_outcome` + `outcome`)
#   - "kind":"cycle"                 §10.1 meristem/loop.py: "kind:\"cycle\" 记录
#                                     由土壤 supervisor ... 生成" (line ~716)
#   - "kind":"prompt_over_budget"    §10.1 engine.py budget guard (line ~748)
#
# NOTE: "kind":"probe" (§8.3) belongs to the *scoreboard*
# (state/soil-scoreboard.jsonl), a different file/schema than the ledger
# (§8.2 vs §8.3). CA-6's authority source is explicitly "§8.2 + §10"
# (the ledger + pipeline.py), so "probe" is deliberately excluded here.
#
# "kind":"fitness" is explicitly a RETIRED/never-valid value (§8.2 erratum,
# §17.7 retired-word list) -- it must NOT appear in this set.
SPEC_DECLARED_LEDGER_KINDS = frozenset({
    "observed_fitness",
    "promotion_intent",
    "accepted_fitness",
    "promotion_committed",
    "promotion_outcome",
    "cycle",
    "prompt_over_budget",
})


# ---------------------------------------------------------------------------
# Misc small helpers shared by CA tests
# ---------------------------------------------------------------------------

def load_jsonl(path: Path) -> list[dict]:
    import json
    rows = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def iter_py_files(*dirs: Path):
    for d in dirs:
        if d.is_dir():
            yield from d.rglob("*.py")


def path_has_symlink_or_bad_hardlink(p: Path, protected_dirs: list[Path]) -> str | None:
    """Return a violation description, or None if p is clean.

    Checks: p itself is not a symlink; and if the platform's stat exposes
    st_nlink > 1 (hardlink), best-effort-check its target doesn't resolve
    into one of protected_dirs. Hardlink target identity can't be fully
    verified cross-platform without matching inode+device against every file
    in protected_dirs, so this is best-effort (documented, not silently
    assumed complete).
    """
    if p.is_symlink():
        return f"{p} is a symlink"
    try:
        st = p.stat()
    except OSError:
        return None
    if getattr(st, "st_nlink", 1) > 1:
        try:
            resolved = p.resolve()
        except OSError:
            resolved = p
        for pd in protected_dirs:
            try:
                resolved.relative_to(pd.resolve())
                return f"{p} has st_nlink>1 and resolves under protected dir {pd}"
            except ValueError:
                continue
    return None
