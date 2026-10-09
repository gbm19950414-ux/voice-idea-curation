#!/usr/bin/env python3
"""Append a different-prompt check without replacing a primary transcript or approval."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from asr_support import file_lock, now, object_hash, read_json, sha256
from local_asr import RecognitionError, recognize
from scan_and_transcribe import attempts, save_attempt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inbox", type=Path, required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--recording-id", required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    if Path(args.batch_id).name != args.batch_id:
        parser.error("invalid batch-id")
    try:
        with file_lock(args.inbox / ".intake.lock"):
            batch = read_json(args.inbox / "batches" / args.batch_id / "batch.json")
            source = next(s for s in batch["sources"] if str(s["id"]) == args.recording_id)
            path = Path(source.get("current_path") or source["source_path"])
            source_hash = sha256(path)
            if source_hash != (source.get("sha256") or source.get("source_sha256")):
                raise ValueError("source hash changed")
            config = read_json(args.config)
            config.update({"personalization_enabled": False, "prompt_terms": [], "prompt": ""})
            config.pop("profile_path", None)
            fingerprint = object_hash({"config": config, "adapter_sha256": sha256(Path(__file__).with_name("local_asr.py"))})
            directory = args.inbox / "batches" / args.batch_id / "raw"
            prior = next(((p, r) for p, r in reversed(attempts(directory, args.recording_id)) if r.get("stage") == "verification" and r.get("recognition_identity") == fingerprint and r.get("source_sha256") == source_hash), None)
            if prior:
                print(json.dumps({"status": "already_checked", "attempt_path": str(prior[0]), "text": prior[1].get("response", {}).get("text", "")}, ensure_ascii=False))
                return 0
            started = now()
            try:
                response = recognize(path, config)
            except RecognitionError as error:
                response = {"text": "", "segments": [], "error": {"kind": error.kind, "message": str(error), "retryable": error.retryable}}
            record = {"id": args.recording_id, "source_sha256": source_hash, "stage": "verification", "recognition_identity": fingerprint,
                      "configuration": config, "started_at_utc": started, "finished_at_utc": now(), "engine": "local-asr verification",
                      "status": "success" if response.get("text") and not response.get("error") else "failed", "response": response}
            target = save_attempt(directory, args.recording_id, record)
            print(json.dumps({"status": record["status"], "attempt_path": str(target), "text": response.get("text", ""), "error": response.get("error")}, ensure_ascii=False))
            return 0
    except (ValueError, OSError, KeyError, StopIteration, RuntimeError) as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
