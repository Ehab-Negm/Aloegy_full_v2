"""Archive current QA batch artifacts before starting a clean run."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_TELEMETRY = Path(".runtime/prod/telemetry.jsonl")
DEFAULT_MATRIX = Path("../docs/qa-call-matrix-live.csv")


def _archive_path(path: Path, archive_dir: Path, stamp: str) -> Path:
    return archive_dir / f"{path.name}.{stamp}.bak"


def reset_batch(*, telemetry: Path, matrix: Path, archive_dir: Path) -> dict:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_dir.mkdir(parents=True, exist_ok=True)
    archived: list[dict[str, str]] = []
    missing: list[str] = []
    for path in (telemetry, matrix):
        if not path.exists():
            missing.append(str(path))
            continue
        target = _archive_path(path, archive_dir, stamp)
        shutil.move(str(path), str(target))
        archived.append({"from": str(path), "to": str(target)})
    return {
        "archived": archived,
        "missing": missing,
        "archive_dir": str(archive_dir),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Archive current QA batch artifacts.")
    parser.add_argument("--telemetry", type=Path, default=DEFAULT_TELEMETRY)
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--archive-dir", type=Path, default=Path(".runtime/prod/archive"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    result = reset_batch(
        telemetry=args.telemetry,
        matrix=args.matrix,
        archive_dir=args.archive_dir,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
