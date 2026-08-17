#!/usr/bin/env python3
"""Standalone report formatter. Reads JSON from stdin, writes REPORT.md.

This is NOT an organ -- no organ.json, no lifecycle, no germline.invoke.
It is a formatting tool externalized from the kernel to keep the core small.
Must NOT import from meristem.
"""
import json
import pathlib
import sys


def main() -> int:
    data = json.loads(sys.stdin.read())
    workdir = pathlib.Path(data["workdir"])

    core_pressure = data.get("core_pressure", 0)
    closure_pressure = data.get("closure_pressure", 0)
    prev_core = data.get("prev_core_pressure")
    prev_closure = data.get("prev_closure_pressure")

    if prev_core is not None:
        core_arrow = "\u2191" if core_pressure > prev_core else (
            "\u2193" if core_pressure < prev_core else "\u2192")
    else:
        core_arrow = "\u2192"
    if prev_closure is not None:
        closure_arrow = "\u2191" if closure_pressure > prev_closure else (
            "\u2193" if closure_pressure < prev_closure else "\u2192")
    else:
        closure_arrow = "\u2192"

    outcomes = data.get("outcomes", {})
    recent_count = data.get("recent_count", 0)

    lines = ["# Meristem Report", ""]
    lines.append(f"Generated: {data.get('generated_at', '')}")
    lines.append("")
    lines.append("## Cycles since last report")
    if recent_count:
        lines.append(f"Total: {recent_count}")
        for outcome, count in sorted(outcomes.items()):
            lines.append(f"  {outcome}: {count}")
    else:
        lines.append("(none)")
    lines.append("")
    lines.append("## Pressures")
    lines.append(f"  core:    {core_pressure:.2f} {core_arrow}")
    lines.append(f"  closure: {closure_pressure:.2f} {closure_arrow}")
    lines.append("")
    lines.append("## Acceptance")
    lines.append(f"  AGR (self-proposed / total accepted): {data.get('agr', '0/0')}")
    lines.append("")
    lines.append("## Open proposals")
    lines.append(f"  count: {data.get('open_proposals', 0)}")
    lines.append("")
    lines.append("## Parked tasks")
    parked = data.get("parked", [])
    if parked:
        for task in sorted(parked):
            lines.append(f"  - {task[:80]}")
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append("## Mailbox")
    mailbox_items = data.get("mailbox_items", [])
    if mailbox_items:
        for item in mailbox_items:
            lines.append(f"  - {item[:80]}")
    else:
        lines.append("  (empty)")
    lines.append("")
    lines.append("## Probe scores")
    probe_scores = data.get("probe_scores", {})
    if probe_scores:
        for probe_id in sorted(probe_scores):
            lines.append(f"  {probe_id}: {probe_scores[probe_id]:.2f}")
    else:
        lines.append("  (none)")
    lines.append("")

    report_path = workdir / "REPORT.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"ok": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
