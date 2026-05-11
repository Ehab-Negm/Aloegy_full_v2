"""Re-aggregate _summary.json + _summary.md from the per-scenario JSON files
already on disk, without re-running the LLM. Useful after partial reruns.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Reuse the aggregator from the runner.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from qa_text_batch_runner import aggregate, _summary_md, RESULTS_DIR


def main() -> int:
    reports = []
    for p in sorted(RESULTS_DIR.glob("PK-*.json")):
        try:
            reports.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception as exc:
            print(f"failed to load {p.name}: {exc}", file=sys.stderr)
    if not reports:
        print("no PK-*.json reports found", file=sys.stderr)
        return 1
    summary = aggregate(reports)
    (RESULTS_DIR / "_summary.json").write_text(
        json.dumps({"summary": summary, "reports": reports}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (RESULTS_DIR / "_summary.md").write_text(_summary_md(summary, reports), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print(
        f"aggregated {len(reports)} reports | pass_rate={summary['pass_rate_pct']}% "
        f"({summary['passed']}/{summary['total']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
