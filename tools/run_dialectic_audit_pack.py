#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from audit_evidence import ROOT, now_iso, resolve_path, today_tag, write_json


def run_step(name: str, command: list[str], root: Path) -> dict[str, Any]:
    completed = subprocess.run(command, cwd=root, text=True, capture_output=True, timeout=180, check=False)
    return {
        "name": name,
        "command": command,
        "exit_code": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "passed": completed.returncode == 0,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Dialectic Audit Pack Run",
        "",
        f"Generated: `{report.get('generated_at')}`",
        f"Decision: `{report.get('decision')}`",
        f"Can trade: `{report.get('can_trade')}`",
        "",
        "## Steps",
        "",
    ]
    for item in report.get("steps") or []:
        lines.append(
            f"- `{item.get('name')}`: passed=`{item.get('passed')}`, exit_code=`{item.get('exit_code')}`, stdout=`{item.get('stdout')}`"
        )
    lines.extend(["", "## Artifacts", ""])
    for name, path in (report.get("artifacts") or {}).items():
        lines.append(f"- `{name}`: `{path}`")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run fresh Devil, Angel and Dialectic reports as one read-only pack.")
    parser.add_argument("--active-root", default=str(ROOT))
    parser.add_argument("--tag", default=today_tag())
    parser.add_argument("--out-prefix")
    args = parser.parse_args()
    root = resolve_path(args.active_root)
    tag = args.tag
    devil_prefix = f"docs/FULL_SYSTEM_DEVIL_AUDIT_{tag}_DIALECTIC_PACK"
    angel_prefix = f"docs/FULL_SYSTEM_ANGEL_AUDIT_{tag}"
    synthesis_prefix = f"docs/DIALECTIC_SYNTHESIS_{tag}"
    pack_prefix = args.out_prefix or f"docs/DIALECTIC_AUDIT_PACK_{tag}"

    steps = [
        run_step(
            "devil_audit",
            [sys.executable, "tools/full_system_devil_audit.py", "--active-root", str(root), "--out-prefix", devil_prefix],
            root,
        ),
        run_step(
            "angel_audit",
            [
                sys.executable,
                "tools/full_system_angel_audit.py",
                "--active-root",
                str(root),
                "--devil-report",
                f"{devil_prefix}.json",
                "--out-prefix",
                angel_prefix,
            ],
            root,
        ),
        run_step(
            "dialectic_synthesis",
            [
                sys.executable,
                "tools/dialectic_synthesizer.py",
                "--active-root",
                str(root),
                "--devil-report",
                f"{devil_prefix}.json",
                "--angel-report",
                f"{angel_prefix}.json",
                "--out-prefix",
                synthesis_prefix,
            ],
            root,
        ),
    ]
    failed_steps = [item["name"] for item in steps if not item["passed"]]
    report = {
        "generated_at": now_iso(),
        "tool": "run_dialectic_audit_pack",
        "decision": "dialectic_audit_pack_completed" if not failed_steps else "dialectic_audit_pack_failed",
        "steps": steps,
        "failed_steps": failed_steps,
        "artifacts": {
            "devil_json": f"{devil_prefix}.json",
            "devil_md": f"{devil_prefix}.md",
            "angel_json": f"{angel_prefix}.json",
            "angel_md": f"{angel_prefix}.md",
            "synthesis_json": f"{synthesis_prefix}.json",
            "synthesis_md": f"{synthesis_prefix}.md",
        },
        "runtime_boundary": {
            "audit_only": True,
            "alerts_allowed": False,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "uses_private_credentials": False,
        },
        "can_trade": False,
    }
    prefix = resolve_path(pack_prefix, root)
    write_json(prefix.with_suffix(".json"), report)
    prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "failed_steps": failed_steps,
                "artifacts": report["artifacts"],
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not failed_steps else 1


if __name__ == "__main__":
    raise SystemExit(main())
