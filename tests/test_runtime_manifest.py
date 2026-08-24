import json
import os
import tempfile
import unittest
from pathlib import Path

from substrate.runtime_manifest import RuntimeManifestError, refresh, verify


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


if __name__ == "__main__":
    unittest.main()
