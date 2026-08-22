"""Probe gate: layered regression + the internal/external divergence alarm.

This is the ONLY module that may read the eval vault. Rubrics, held-outs and
golden fixtures live outside the repository so the mutation engine cannot
read them -- physical invisibility beats asking a prompt not to look.

Layering exists because frozen probes grow monotonically by design: running
the whole set every cycle would make cost rise linearly with the number of
sentinels. Per cycle we run the target probe plus a rotating sample; the
full frozen set runs before promotion, which is rarer and human-gated.

Rubrics prefer an executable check.py over model judgement: grounding in
verification beats grounding in opinion.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field

from .. import SCOREBOARD, VAULT, read_json, read_jsonl

SAMPLE_SIZE = 3


@dataclass
class ProbeRun:
    probe_id: str
    score: float
    domain: str = ""
    kind: str = "internal"
    detail: str = ""
    before: float | None = None
    status: str = "unmeasured"


@dataclass
class ProbeVerdict:
    passed: bool = True
    runs: list[ProbeRun] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    alarm: str = ""

    def fail(self, reason: str) -> None:
        self.passed = False
        self.failures.append(reason)


def _dir(kind: str):
    return VAULT / ("anchors" if kind == "anchor" else "internal/active")


def catalogue(kind: str = "internal") -> list[dict]:
    """Every probe of a kind: its manifest, not its rubric."""
    base = _dir(kind)
    if not base.is_dir():
        return []
    out = []
    for entry in sorted(base.iterdir()):
        manifest = read_json(entry / "probe.json")
        if manifest:
            manifest.setdefault("id", entry.name)
            manifest["_path"] = str(entry)
            manifest["_kind"] = kind
            out.append(manifest)
    return out


def probe_exists(probe_id: str) -> bool:
    """Is this probe id present in the vault?

    An organ may not reach register/active while naming probes that do not
    exist -- otherwise "it has probes" is satisfied by a string, and growth
    without a measuring stick passes as growth with one.
    """
    return any(
        manifest.get("id") == probe_id
        for kind in ("internal", "anchor")
        for manifest in catalogue(kind)
    )


def missing_probes(names) -> list[str]:
    return sorted({name for name in (names or []) if not probe_exists(name)})


def run_probe(manifest: dict, workdir: str) -> ProbeRun:
    """Score one probe. An executable check.py is authoritative when present."""
    import pathlib

    # A rubric runs with its own directory as cwd, so a relative workdir would
    # resolve against the vault instead of the candidate. Always hand it an
    # absolute path.
    workdir = str(pathlib.Path(workdir).resolve())
    path = pathlib.Path(manifest["_path"])
    check = path / "rubric" / "check.py"
    score, detail = 0.0, ""
    if check.exists():
        try:
            # sys.executable, never a bare name: "python" does not exist on a
            # stock Debian, and a rubric that cannot start scores 0 -- an
            # environment difference would read as a capability regression.
            result = subprocess.run(
                [sys.executable, str(check)],
                input=json.dumps({"workdir": workdir, "probe": manifest.get("id")}),
                capture_output=True, text=True, timeout=120, cwd=str(path),
            )
            payload = json.loads(result.stdout or "{}")
            score = float(payload.get("score", 0.0))
            detail = str(payload.get("detail", ""))[:400]
        except (subprocess.SubprocessError, ValueError, json.JSONDecodeError) as exc:
            detail = f"check.py failed: {exc}"
    else:
        detail = "no executable rubric; probe is declarative only"
    return ProbeRun(
        probe_id=manifest.get("id", "?"),
        score=score,
        domain=manifest.get("capability_domain", ""),
        kind=manifest.get("_kind", "internal"),
        detail=detail,
    )


def select(target: str | None, full: bool, cycle: int) -> list[dict]:
    """Layered selection: full set before promotion, sample per cycle."""
    probes = catalogue("internal") + catalogue("anchor")
    if full:
        return probes
    chosen = [p for p in probes if p.get("id") == target]
    rest = [p for p in probes if p.get("id") != target]
    if rest:
        start = (cycle * SAMPLE_SIZE) % len(rest)
        rotation = rest[start:] + rest[:start]
        chosen += rotation[:SAMPLE_SIZE]
    return chosen


def baseline() -> dict:
    """Latest recorded score per probe from the append-only scoreboard."""
    latest: dict = {}
    for row in read_jsonl(SCOREBOARD):
        if row.get("kind") == "probe":
            latest[row.get("probe_id")] = float(row.get("score", 0.0))
    return latest


def divergence_alarm(runs: list[ProbeRun], history: dict) -> str:
    """Matched-domain divergence: internal up while its anchor is flat/down.

    Domain matching is load-bearing. A global comparison fires on unrelated
    noise, and this alarm is fail-closed -- false positives would repeatedly
    strip earned autonomy and train the human to rubber-stamp re-promotions.
    Alarm fatigue on a fail-closed mechanism is a weakening-by-erosion path.
    """
    by_domain: dict[str, dict[str, list[float]]] = {}
    for run in runs:
        if not run.domain:
            continue
        slot = by_domain.setdefault(run.domain, {"internal": [], "anchor": []})
        slot[run.kind if run.kind == "anchor" else "internal"].append(
            run.score - history.get(run.probe_id, run.score)
        )
    for domain, deltas in by_domain.items():
        if not deltas["internal"] or not deltas["anchor"]:
            continue
        internal = sum(deltas["internal"]) / len(deltas["internal"])
        anchor = sum(deltas["anchor"]) / len(deltas["anchor"])
        if internal > 0 and anchor <= 0:
            return (
                f"divergence in domain '{domain}': internal +{internal:.2f} "
                f"while anchor {anchor:+.2f} -- suspected gaming"
            )
    return ""


def run(workdir: str, *, target: str | None = None, full: bool = False,
        cycle: int = 0) -> ProbeVerdict:
    verdict = ProbeVerdict()
    history = baseline()
    for manifest in select(target, full, cycle):
        result = run_probe(manifest, workdir)
        verdict.runs.append(result)
        previous = history.get(result.probe_id)
        result.before, result.status = previous, ("baseline" if previous is None else "improved" if result.score > previous else "regressed" if result.score < previous else "no_regression")
        if previous is not None and result.score < previous:
            verdict.fail(
                f"regression on frozen probe '{result.probe_id}': "
                f"{previous:.2f} -> {result.score:.2f}"
            )
        if target and result.probe_id == target and previous is not None:
            if result.score <= previous:
                verdict.fail(
                    f"target probe '{target}' did not improve "
                    f"({previous:.2f} -> {result.score:.2f})"
                )
    verdict.alarm = divergence_alarm(verdict.runs, history)
    if verdict.alarm:
        verdict.fail(verdict.alarm)
    return verdict
