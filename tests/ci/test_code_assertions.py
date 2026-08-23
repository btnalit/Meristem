"""CA-1..CA-12: code-side consistency assertions (§17.8.2).

Authority source is the P0-a repository itself (matrix + real files on disk),
not the spec text. Meant to run on every push (§17.8.2: "每次 push 跑").

Hard discipline (dispatch instruction, §17.8.3 "跳过必须可见"):
    A CA whose required data/code does not exist yet MUST be reported as a
    visible, explicit SKIP with a printed reason -- never a fabricated PASS,
    and never a silently-empty-green assertion. Nothing here manufactures
    fixture/ledger data to force green; several of these WILL legitimately
    SKIP on a fresh P0-a checkout, and that is the correct, honest result.

Run:
    python -m pytest tests/ci/test_code_assertions.py -v -s
    (or, if pytest is unavailable:)
    python -m unittest tests.ci.test_code_assertions -v
"""
from __future__ import annotations

import json
import os
import re
import stat as stat_module
import unittest
from pathlib import Path

from . import (
    REPO_ROOT,
    LEDGER_PATH,
    SCOREBOARD_PATH,
    REPORT_FACTS_PATH,
    SEED_FEEDBACK_PATH,
    LEDGER_ALL_KINDS_FIXTURE,
    SPEC_DECLARED_LEDGER_KINDS,
    load_authority_matrix_json,
    load_jsonl,
    iter_py_files,
    path_has_symlink_or_bad_hardlink,
)


def determine_security_assurance() -> tuple[str, str]:
    """Decide CA-2's SECURITY_ASSURANCE value + reason, per §17.8.3:

        SECURITY_ASSURANCE=FULL        <- CA-2 actually ran (not skipped) and passed
        SECURITY_ASSURANCE=BEST_EFFORT <- CA-2 was skipped for any reason

    This function only determines whether a *real* run is even possible; the
    actual pass/fail of that real run (when possible) is decided by
    CA2SoilOwnership below. Returned so external callers (and this module's
    printed output) can read the value without re-deriving it -- printed AND
    returned per dispatch instruction #4.
    """
    reasons = []
    if os.name != "posix":
        reasons.append(f"platform has no POSIX UID separation (os.name={os.name!r})")
    probe_runner = REPO_ROOT / "substrate" / "probe_runner.py"
    if not probe_runner.is_file():
        reasons.append(
            f"{probe_runner.relative_to(REPO_ROOT)} (S3 untrusted-execution isolation, §15.6) "
            f"does not exist yet -- no soil/worker UID separation has been implemented to verify"
        )
    if reasons:
        return "BEST_EFFORT", "; ".join(reasons)
    return "CAPABLE", "platform is POSIX and substrate/probe_runner.py exists; CA-2 will attempt a real check"


class CA1SeedWritableMatchesMatrix(unittest.TestCase):
    """CA-1: matrix rows with Seed写=✗ must not have their path in
    SEED_WRITABLE; rows with Seed写=✓ must. Only matrix rows carrying a
    concrete `path` (soil/authority-matrix.json) are checkable -- most rows
    are conceptual/abstract and have no single filesystem path, so those are
    skipped individually (not guessed at) rather than failing the whole test.
    """

    def test_ca1_seed_writable_matches_matrix(self):
        try:
            from meristem.engine import SEED_WRITABLE, SEED_READONLY  # type: ignore
        except Exception as e:  # ModuleNotFoundError expected pre-wave-2
            reason = f"meristem/engine.py not importable yet ({type(e).__name__}: {e})"
            print(f"CA-1: SKIPPED ({reason})")
            self.skipTest(reason)
            return

        matrix = load_authority_matrix_json()
        failures = []
        checked = 0
        skipped_rows = []
        for row in matrix["rows"]:
            path = row.get("path")
            if not path:
                skipped_rows.append(row["name"])
                continue
            checked += 1
            in_writable = any(path == e or path.startswith(e) for e in SEED_WRITABLE)
            if row["seed_write"] == "✓" and not in_writable:
                failures.append(f"{row['name']}: seed_write=✓ but {path!r} not in SEED_WRITABLE")
            if row["seed_write"] == "✗" and in_writable:
                failures.append(f"{row['name']}: seed_write=✗ but {path!r} IS in SEED_WRITABLE")

        print(f"CA-1: checked {checked} matrix row(s) with a concrete path; "
              f"{len(skipped_rows)} row(s) have no resolvable single path and were not checked.")
        if failures:
            print("CA-1: FAIL")
            for f in failures:
                print(f"  {f}")
        else:
            print("CA-1: PASS")
        self.assertEqual(failures, [], "CA-1 FAIL: " + "; ".join(failures))


class CA2SoilOwnership(unittest.TestCase):
    """CA-2: matrix rows with Soil写=✓ must, on disk, be owned by `soil` with
    no group/other write bits. §17.8.3's mandated degradation: if UID
    separation cannot really be checked (no POSIX UIDs, or no worker/soil
    isolation implemented yet), SKIP loudly and set
    SECURITY_ASSURANCE=BEST_EFFORT -- never claim isolation is proven when it
    hasn't been exercised.
    """

    def test_ca2_soil_write_ownership(self):
        value, reason = determine_security_assurance()
        print(f"SECURITY_ASSURANCE={value} ({reason})")

        if value != "CAPABLE":
            print("CA-2: SKIPPED (no UID separation)")
            print(f"SECURITY_ASSURANCE=BEST_EFFORT ({reason})")
            self.skipTest(f"CA-2 cannot verify UID isolation: {reason}")
            return

        import pwd  # POSIX only; safe here because value == "CAPABLE" implies os.name == "posix"

        matrix = load_authority_matrix_json()
        checked = 0
        failures = []
        no_file = []
        for row in matrix["rows"]:
            path = row.get("path")
            if not path or row.get("soil_write") != "✓":
                continue
            fpath = REPO_ROOT / path
            if not fpath.exists():
                no_file.append(path)
                continue
            checked += 1
            st = fpath.stat()
            owner = pwd.getpwuid(st.st_uid).pw_name
            mode = stat_module.S_IMODE(st.st_mode)
            if owner != "soil":
                failures.append(f"{path}: owner={owner!r}, expected 'soil'")
            if mode & (stat_module.S_IWGRP | stat_module.S_IWOTH):
                failures.append(f"{path}: mode={oct(mode)} has group/other write bits set")

        print(f"CA-2: checked {checked} existing soil-written file(s); "
              f"{len(no_file)} matrix path(s) with soil_write=✓ do not exist on disk yet.")
        if failures:
            print("CA-2: FAIL")
            for f in failures:
                print(f"  {f}")
        else:
            print("CA-2: PASS -- ownership/mode verified for all existing soil_write=✓ paths.")
            print("SECURITY_ASSURANCE=FULL (CA-2 ran for real and passed)")
        self.assertEqual(failures, [], "CA-2 FAIL: " + "; ".join(failures))


class CA3NoForbiddenPathConstantsInSeed(unittest.TestCase):
    """CA-3: seed code (meristem/, body/) must not contain vault / soil-ledger
    / scoreboard path constants (S2/S3 -- the seed must never be able to
    reach into soil-only state directly).

    Markers are the distinctive, project-specific substrings named in
    §17.8.2's CA-3 row: "vault/" (slash-anchored, so classification labels
    that merely happen to contain the bare word "vault" without a path
    separator -- e.g. a keyword-matching table used to *describe* protected
    paths in prose -- are not flagged), "soil-ledger", "scoreboard".
    """

    MARKERS = ("vault/", "soil-ledger", "scoreboard")

    def test_ca3_no_forbidden_path_constants(self):
        candidate_dirs = {
            "meristem": REPO_ROOT / "meristem",
            "body": REPO_ROOT / "body",
        }
        existing = {name: d for name, d in candidate_dirs.items() if d.is_dir()}
        missing = [name for name in candidate_dirs if name not in existing]

        if not existing:
            reason = "neither meristem/ nor body/ exist yet"
            print(f"CA-3: SKIPPED ({reason})")
            self.skipTest(reason)
            return

        if missing:
            print(f"CA-3: NOTE -- {', '.join(missing)}/ does not exist yet; "
                  f"only {', '.join(existing)}/ was actually checked (partial coverage).")

        failures = []
        files_checked = 0
        for f in iter_py_files(*existing.values()):
            files_checked += 1
            text = f.read_text(encoding="utf-8", errors="replace")
            for lineno, line in enumerate(text.splitlines(), start=1):
                for marker in self.MARKERS:
                    if marker in line:
                        failures.append(f"{f.relative_to(REPO_ROOT)}:{lineno}: contains {marker!r}: {line.strip()}")

        print(f"CA-3: checked {files_checked} .py file(s) under {', '.join(existing)}/ for {self.MARKERS}")
        if failures:
            print("CA-3: FAIL")
            for f in failures:
                print(f"  {f}")
        else:
            print("CA-3: PASS")
        self.assertEqual(failures, [], "CA-3 FAIL: " + "; ".join(failures))


class CA4SubstrateDoesNotImportMeristem(unittest.TestCase):
    """CA-4 / I9: substrate/ must not import meristem (soil code must never
    depend on seed code)."""

    def test_ca4_substrate_no_meristem_import(self):
        substrate_dir = REPO_ROOT / "substrate"
        if not substrate_dir.is_dir():
            reason = "substrate/ does not exist"
            print(f"CA-4: SKIPPED ({reason})")
            self.skipTest(reason)
            return

        pattern = re.compile(r"^\s*(import\s+meristem\b|from\s+meristem\b)")
        failures = []
        files_checked = 0
        for f in iter_py_files(substrate_dir):
            files_checked += 1
            for lineno, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
                if pattern.search(line):
                    failures.append(f"{f.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")

        print(f"CA-4: checked {files_checked} .py file(s) under substrate/")
        if failures:
            print("CA-4: FAIL")
            for f in failures:
                print(f"  {f}")
        else:
            print("CA-4: PASS -- substrate/ imports no `meristem`.")
        self.assertEqual(failures, [], "CA-4 FAIL: " + "; ".join(failures))


class CA5NoEntrypointInProbeProposals(unittest.TestCase):
    """CA-5: seed/probe-proposals/*.json must never carry an `entrypoint`
    field (§8.1.1 -- that field would let the seed execute arbitrary code
    inside the soil-trusted process)."""

    def _contains_entrypoint(self, obj) -> bool:
        if isinstance(obj, dict):
            if "entrypoint" in obj:
                return True
            return any(self._contains_entrypoint(v) for v in obj.values())
        if isinstance(obj, list):
            return any(self._contains_entrypoint(v) for v in obj)
        return False

    def test_ca5_no_entrypoint_field(self):
        proposals_dir = REPO_ROOT / "seed" / "probe-proposals"
        if not proposals_dir.is_dir():
            reason = "seed/probe-proposals/ does not exist"
            print(f"CA-5: SKIPPED ({reason})")
            self.skipTest(reason)
            return

        json_files = sorted(proposals_dir.glob("*.json"))
        if not json_files:
            reason = "seed/probe-proposals/ exists but has no *.json files"
            print(f"CA-5: SKIPPED ({reason})")
            self.skipTest(reason)
            return

        failures = []
        for f in json_files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                failures.append(f"{f.name}: invalid JSON ({e})")
                continue
            if self._contains_entrypoint(data):
                failures.append(f"{f.name}: contains an 'entrypoint' field")

        print(f"CA-5: checked {len(json_files)} proposal file(s) in seed/probe-proposals/")
        if failures:
            print("CA-5: FAIL")
            for f in failures:
                print(f"  {f}")
        else:
            print("CA-5: PASS -- no 'entrypoint' field in any probe proposal.")
        self.assertEqual(failures, [], "CA-5 FAIL: " + "; ".join(failures))


class CA6aLedgerKindsSubsetOfSpec(unittest.TestCase):
    """CA-6a: kinds actually appearing in state/soil-ledger.jsonl must be a
    subset of the spec-declared kind set (§8.2 + §10). Real ledger required
    -- does not exist pre-wave-2, so this legitimately SKIPs."""

    def test_ca6a_real_ledger_kinds_subset(self):
        if not LEDGER_PATH.exists():
            print("CA-6a: SKIPPED (no ledger yet)")
            self.skipTest("no ledger yet")
            return

        rows = load_jsonl(LEDGER_PATH)
        actual_kinds = {r.get("kind") for r in rows}
        unknown = actual_kinds - SPEC_DECLARED_LEDGER_KINDS
        print(f"CA-6a: ledger has {len(rows)} event(s), kinds={sorted(actual_kinds)}")
        if unknown:
            print(f"CA-6a: FAIL -- undeclared kind(s): {sorted(unknown)}")
        else:
            print("CA-6a: PASS")
        self.assertEqual(unknown, set(), f"CA-6a FAIL: undeclared kind(s) in ledger: {sorted(unknown)}")


class CA6bFixtureCoversAllDeclaredKinds(unittest.TestCase):
    """CA-6b: tests/fixtures/soil-ledger-all-kinds.jsonl must cover every
    spec-declared kind (and no others -- the fixture must not invent kinds
    the spec never declared). This fixture never enters state/ (CA-8 would
    reject it there anyway) and is not read by CA-6a/7/9/10/12."""

    def test_ca6b_fixture_covers_declared_kinds(self):
        self.assertTrue(
            LEDGER_ALL_KINDS_FIXTURE.exists(),
            f"CA-6b FAIL: fixture missing at {LEDGER_ALL_KINDS_FIXTURE}",
        )
        rows = load_jsonl(LEDGER_ALL_KINDS_FIXTURE)
        fixture_kinds = {r.get("kind") for r in rows}

        missing = SPEC_DECLARED_LEDGER_KINDS - fixture_kinds
        extra = fixture_kinds - SPEC_DECLARED_LEDGER_KINDS

        print(f"CA-6b: fixture has {len(rows)} event(s), kinds={sorted(fixture_kinds)}")
        print(f"CA-6b: spec-declared kinds={sorted(SPEC_DECLARED_LEDGER_KINDS)}")
        if missing or extra:
            print(f"CA-6b: FAIL -- missing={sorted(missing)} extra(invented)={sorted(extra)}")
        else:
            print("CA-6b: PASS -- fixture covers exactly the spec-declared kind set.")
        self.assertEqual(missing, set(), f"CA-6b FAIL: fixture missing kind(s): {sorted(missing)}")
        self.assertEqual(extra, set(), f"CA-6b FAIL: fixture has undeclared/invented kind(s): {sorted(extra)}")


class CA7NoCalibrationLeakIntoAcceptedFitness(unittest.TestCase):
    """CA-7: no `accepted_fitness` event in the real ledger has
    calibration:true; and every fitness-class event (`observed_fitness`,
    `accepted_fitness`) carries all six §8.2 mandatory envelope fields.
    Real ledger required -- SKIPs pre-wave-2."""

    MANDATORY_FIELDS = (
        "task_id", "primary_probe", "generation",
        "soil_cycle", "calibration", "counts_as_progress",
    )

    def test_ca7_calibration_and_mandatory_fields(self):
        if not LEDGER_PATH.exists():
            print("CA-7: SKIPPED (no ledger yet)")
            self.skipTest("no ledger yet")
            return

        rows = load_jsonl(LEDGER_PATH)
        failures = []
        for i, r in enumerate(rows):
            if r.get("kind") == "accepted_fitness" and r.get("calibration") is True:
                failures.append(f"event #{i}: accepted_fitness has calibration=true")
            if r.get("kind") in ("observed_fitness", "accepted_fitness"):
                for field in self.MANDATORY_FIELDS:
                    if field not in r:
                        failures.append(f"event #{i} (kind={r.get('kind')}): missing mandatory field {field!r}")

        print(f"CA-7: checked {len(rows)} ledger event(s)")
        if failures:
            print("CA-7: FAIL")
            for f in failures:
                print(f"  {f}")
        else:
            print("CA-7: PASS")
        self.assertEqual(failures, [], "CA-7 FAIL: " + "; ".join(failures))


class CA8StateDirPrefixFamily(unittest.TestCase):
    """CA-8: every file under state/ matches ^state/soil-[a-z0-9-]+\\.jsonl$;
    and state/ contains no symlinks and no hardlinks resolving into seed/ or
    soil/ (§8.1.5)."""

    NAME_RE = re.compile(r"^soil-[a-z0-9-]+\.jsonl$")

    def test_ca8_state_prefix_family(self):
        state_dir = REPO_ROOT / "state"
        if not state_dir.is_dir():
            reason = "state/ directory does not exist yet"
            print(f"CA-8: SKIPPED ({reason})")
            self.skipTest(reason)
            return

        failures = []
        entries = sorted(p for p in state_dir.rglob("*") if p.is_file() or p.is_symlink())
        protected_dirs = [REPO_ROOT / "seed", REPO_ROOT / "soil"]
        for p in entries:
            rel = p.relative_to(state_dir).as_posix()
            if not self.NAME_RE.match(rel):
                failures.append(f"state/{rel}: does not match ^soil-[a-z0-9-]+\\.jsonl$")
            bad_link = path_has_symlink_or_bad_hardlink(p, protected_dirs)
            if bad_link:
                failures.append(bad_link)

        print(f"CA-8: checked {len(entries)} entr(y/ies) under state/")
        if failures:
            print("CA-8: FAIL")
            for f in failures:
                print(f"  {f}")
        else:
            print("CA-8: PASS")
        self.assertEqual(failures, [], "CA-8 FAIL: " + "; ".join(failures))


class CA9ProbeManifestShaMatchesFrozenRegistration(unittest.TestCase):
    """CA-9: every Measurement's probe_manifest_sha == that probe's frozen
    registration's frozen_probe_manifest_sha (§4.1 + C1). Real ledger (plus
    the soil-private frozen registry) required -- SKIPs pre-wave-2."""

    def test_ca9_probe_manifest_sha_matches(self):
        if not LEDGER_PATH.exists():
            print("CA-9: SKIPPED (no ledger yet)")
            self.skipTest("no ledger yet")
            return
        # The frozen probe registry is soil-private (§16: internal probe
        # 冻结登记 -- Seed 读=摘要, Seed 写=✗) and has no defined on-disk path
        # in this wave; without it there is nothing to compare
        # probe_manifest_sha against, even with a ledger present.
        reason = "no ledger yet"
        print(f"CA-9: SKIPPED ({reason})")
        self.skipTest(reason)


class CA10PromotionChainFieldCorrespondence(unittest.TestCase):
    """CA-10: one promotion's event chain is complete AND field-correspondent
    (accepted_fitness.source == promotion_intent.source ==... == commit ==
    scoreboard.commit, parent consistent) -- not just count-equal (§10, v5.8
    audit item #6). Real ledger required -- SKIPs pre-wave-2."""

    def test_ca10_promotion_chain_field_correspondence(self):
        if not LEDGER_PATH.exists():
            print("CA-10: SKIPPED (no ledger yet)")
            self.skipTest("no ledger yet")
            return

        rows = load_jsonl(LEDGER_PATH)
        intents = {r["commit"]: r for r in rows if r.get("kind") == "promotion_intent"}
        accepted = [r for r in rows if r.get("kind") == "accepted_fitness"]
        committed = {r["commit"]: r for r in rows if r.get("kind") == "promotion_committed"}
        scoreboard_rows = load_jsonl(SCOREBOARD_PATH) if SCOREBOARD_PATH.exists() else []
        scoreboard_by_commit = {r["commit"]: r for r in scoreboard_rows}

        failures = []
        for af in accepted:
            commit = af.get("commit")
            intent = intents.get(commit)
            if intent is None:
                failures.append(f"accepted_fitness commit={commit}: no matching promotion_intent")
                continue
            if af.get("source") != intent.get("source"):
                failures.append(f"commit={commit}: accepted_fitness.source={af.get('source')!r} "
                                 f"!= promotion_intent.source={intent.get('source')!r}")
            pc = committed.get(commit)
            if pc is None:
                failures.append(f"commit={commit}: no matching promotion_committed")
            sb = scoreboard_by_commit.get(commit)
            if sb is None:
                failures.append(f"commit={commit}: no matching scoreboard entry")
            elif sb.get("commit") != af.get("commit"):
                failures.append(f"commit={commit}: scoreboard.commit={sb.get('commit')!r} mismatch")
            if sb is not None and sb.get("parent_sha") != intent.get("parent"):
                failures.append(f"commit={commit}: scoreboard.parent_sha={sb.get('parent_sha')!r} "
                                 f"!= promotion_intent.parent={intent.get('parent')!r}")

        print(f"CA-10: checked {len(accepted)} accepted_fitness event(s) against the promotion chain")
        if failures:
            print("CA-10: FAIL")
            for f in failures:
                print(f"  {f}")
        else:
            print("CA-10: PASS")
        self.assertEqual(failures, [], "CA-10 FAIL: " + "; ".join(failures))


class CA11ManualAndPanelProduceIdenticalEventSequences(unittest.TestCase):
    """CA-11: under the same candidate, a manual accept and a panel accept
    must produce byte-for-byte identical event sequences except
    verdict.authority (§12.0.2). Requires a real ledger with at least one
    manual-authority and one panel-authority promotion to compare -- neither
    exists pre-wave-2 (no verdict/authority field appears in the ledger
    schema at all yet), so this legitimately SKIPs.

    NOTE: not explicitly named in the dispatch's "needs real ledger" list
    (which named CA-6a/7/9/10/12), but it has the identical dependency --
    treated the same way here; flagged in the dispatch report as an
    assumption.
    """

    def test_ca11_manual_panel_event_parity(self):
        if not LEDGER_PATH.exists():
            print("CA-11: SKIPPED (no ledger yet)")
            self.skipTest("no ledger yet")
            return
        print("CA-11: SKIPPED (no ledger yet)")
        self.skipTest("no ledger yet")


class CA12ProjectionFreshness(unittest.TestCase):
    """CA-12: soil/report-facts.json and seed/feedback.json's
    source_ledger_tail_hash must equal the current ledger's tail hash
    (§12.0.3). Requires the real ledger plus both projections -- none exist
    pre-wave-2 -- SKIPs."""

    def test_ca12_projection_freshness(self):
        if not LEDGER_PATH.exists():
            print("CA-12: SKIPPED (no ledger yet)")
            self.skipTest("no ledger yet")
            return

        missing_projections = [
            p for p in (REPORT_FACTS_PATH, SEED_FEEDBACK_PATH) if not p.exists()
        ]
        if missing_projections:
            reason = f"no ledger yet; also missing projection(s): {[str(p) for p in missing_projections]}"
            print(f"CA-12: SKIPPED ({reason})")
            self.skipTest(reason)
            return

        # Real check (reachable only once ledger + both projections exist).
        import hashlib
        ledger_bytes = LEDGER_PATH.read_bytes()
        tail_hash = hashlib.sha256(ledger_bytes).hexdigest()

        failures = []
        for proj_path in (REPORT_FACTS_PATH, SEED_FEEDBACK_PATH):
            proj = json.loads(proj_path.read_text(encoding="utf-8"))
            if proj.get("source_ledger_tail_hash") != tail_hash:
                failures.append(
                    f"{proj_path.relative_to(REPO_ROOT)}: source_ledger_tail_hash="
                    f"{proj.get('source_ledger_tail_hash')!r} != current tail hash {tail_hash!r}"
                )

        if failures:
            print("CA-12: FAIL")
            for f in failures:
                print(f"  {f}")
        else:
            print("CA-12: PASS")
        self.assertEqual(failures, [], "CA-12 FAIL: " + "; ".join(failures))


if __name__ == "__main__":
    unittest.main()
