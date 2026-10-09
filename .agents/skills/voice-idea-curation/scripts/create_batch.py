#!/usr/bin/env python3
"""Create the empty, resumable record for one voice-idea curation batch."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch_directory", type=Path)
    parser.add_argument("--batch-id", help="Stable batch identifier; defaults to the directory name.")
    parser.add_argument("--skill-version", default="unknown", help="Skill Git commit or version label.")
    args = parser.parse_args()

    batch_dir = args.batch_directory.expanduser().resolve()
    manifest = batch_dir / "batch.json"
    if manifest.exists():
        parser.error(f"refusing to overwrite existing batch record: {manifest}")

    for name in ("raw", "review", "handoff"):
        (batch_dir / name).mkdir(parents=True, exist_ok=True)

    record = {
        "schema_version": 1,
        "batch_id": args.batch_id or batch_dir.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "skill": {"name": "voice-idea-curation", "version": args.skill_version},
        "status": "new",
        "sources": [],
        "items": [],
        "groups": [],
    }
    manifest.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
