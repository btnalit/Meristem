import json
import os
import tempfile
import unittest
from pathlib import Path

from substrate.runtime_manifest import RuntimeManifestError, bootstrap, refresh, verify


class RuntimeManifestTests(unittest.TestCase):
    def test_missing_manifest_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            for rel in ("state/soil-ledger.jsonl", "seed/feedback.json",
                        "soil/report-facts.json", "soil/frozen-probe-registry.json"):
                path = repo / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}\n")
            with self.assertRaises(RuntimeManifestError):
                verify(repo, task_id="task")

    def test_manifest_hashes_and_identity_are_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            paths = {}
            for rel, content in (("state/soil-ledger.jsonl", "{}\n"),
                                 ("seed/feedback.json", "{}\n"),
                                 ("soil/report-facts.json", "{}\n"),
                                 ("soil/frozen-probe-registry.json", "{}\n")):
                path = repo / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
                paths[rel] = path
            import hashlib
            sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
            manifest = {
                "schema_version": 1, "task_id": "task", "generation": "gen-0",
                "ledger_tail_hash": sha(paths["state/soil-ledger.jsonl"]),
                "projection_hashes": {
                    "seed_feedback": sha(paths["seed/feedback.json"]),
                    "report_facts": sha(paths["soil/report-facts.json"]),
                    "frozen_probe_registry": sha(paths["soil/frozen-probe-registry.json"]),
                },
                "ownership": {"owner": "soil", "group": "soil", "modes": {
                    "seed/feedback.json": "0644",
                    "soil/report-facts.json": "0600",
                    "soil/frozen-probe-registry.json": "0644",
                }},
                "fail_closed": {"missing_projection": True, "manifest_mismatch": True},
            }
            manifest_path = repo / "soil/runtime-manifest.json"
            manifest_path.write_text(json.dumps(manifest))
            os.chmod(manifest_path, 0o600)
            os.chmod(paths["seed/feedback.json"], 0o644)
            os.chmod(paths["soil/report-facts.json"], 0o600)
            verify(repo, task_id="task")
            paths["state/soil-ledger.jsonl"].write_text("{\"kind\":\"cycle\"}\n")
            with self.assertRaises(RuntimeManifestError):
                verify(repo, task_id="task")
            refresh(repo, task_id="task")
            verify(repo, task_id="task")
            os.chmod(paths["soil/frozen-probe-registry.json"], 0o666)
            with self.assertRaises(RuntimeManifestError):
                verify(repo, task_id="task")


class RuntimeManifestBootstrapTests(unittest.TestCase):
    """P1-4: nothing today creates soil/runtime-manifest.json (or its
    dependency chain) on a fresh checkout, so verify() can never pass for
    the first time. bootstrap() must fill exactly that gap."""

    def test_bootstrap_on_fresh_checkout_makes_verify_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            data = bootstrap(repo, task_id="task-fresh")
            self.assertEqual(data["task_id"], "task-fresh")
            # verify() is the real acceptance criterion, not bootstrap's
            # own return value.
            verify(repo, task_id="task-fresh")

            ledger = repo / "state" / "soil-ledger.jsonl"
            self.assertTrue(ledger.is_file())
            self.assertEqual(ledger.read_bytes(), b"")
            self.assertTrue((repo / "seed" / "feedback.json").is_file())
            self.assertTrue((repo / "soil" / "report-facts.json").is_file())
            registry = repo / "soil" / "frozen-probe-registry.json"
            self.assertTrue(registry.is_file())
            self.assertEqual(json.loads(registry.read_text(encoding="utf-8")), {})

    def test_bootstrap_preserves_an_existing_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            ledger = repo / "state" / "soil-ledger.jsonl"
            ledger.parent.mkdir(parents=True)
            ledger.write_text(json.dumps({"kind": "cycle", "soil_cycle": 1}) + "\n",
                              encoding="utf-8")
            before = ledger.read_bytes()
            bootstrap(repo, task_id="task-fresh")
            self.assertEqual(ledger.read_bytes(), before)
            verify(repo, task_id="task-fresh")

    def test_bootstrap_refuses_when_manifest_already_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            bootstrap(repo, task_id="task-fresh")
            with self.assertRaises(RuntimeManifestError):
                bootstrap(repo, task_id="task-fresh")


if __name__ == "__main__":
    unittest.main()
