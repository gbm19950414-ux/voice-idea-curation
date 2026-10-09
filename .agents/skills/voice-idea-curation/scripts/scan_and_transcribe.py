#!/usr/bin/env python3
"""Incremental recording intake with immutable attempts and bounded retry."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any
from zoneinfo import ZoneInfo

from asr_support import effective_config, file_lock, now, object_hash, read_json, sha256, write_json
from local_asr import RecognitionError, duration, recognize


def audio_sha256(path: Path, timeout: float = 15) -> str:
    """Bound iCloud materialization so an old placeholder cannot block all intake."""
    result = subprocess.run([sys.executable, '-c',
        'import hashlib,sys;h=hashlib.sha256();f=open(sys.argv[1],"rb");'
        '\nfor chunk in iter(lambda:f.read(1048576),b""):h.update(chunk)\nprint(h.hexdigest())', str(path)],
        capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise OSError(result.stderr[-500:])
    value=result.stdout.strip()
    if not re.fullmatch(r'[a-f0-9]{64}', value):
        raise ValueError('audio read returned invalid SHA256')
    return value


def load_batches(inbox: Path) -> list[tuple[Path, dict[str, Any]]]:
    root = inbox / "batches"
    if not root.exists():
        return []
    found = []
    for directory in sorted(root.iterdir()):
        if not directory.is_dir():
            continue
        manifest = directory / "batch.json"
        if manifest.exists():
            found.append((manifest, read_json(manifest)))
    return found


def index_sources(batches: list[tuple[Path, dict]]) -> dict:
    index = {}
    priority = {"recognized": 3, "success": 3, "failed": 1, "discovered": 0}
    for manifest, batch in batches:
        raw_status = {str(x.get("id")): x.get("status") for x in batch.get("raw_transcriptions", [])}
        for source in batch.get("sources", []):
            digest = source.get("sha256") or source.get("source_sha256")
            if not digest:
                continue
            status = str(source.get("status") or raw_status.get(str(source.get("id"))) or "discovered")
            previous = index.get(digest)
            if previous is None or priority.get(status, 0) > priority.get(previous[2], 0):
                index[digest] = (manifest, source, status)
    return index


def create_batch(inbox: Path, batch_id: str, skill_version: str) -> tuple[Path, dict]:
    manifest = inbox / "batches" / batch_id / "batch.json"
    if manifest.exists():
        return manifest, read_json(manifest)
    return manifest, {"schema_version": 1, "batch_id": batch_id, "created_at": now(),
                      "skill": {"name": "voice-idea-curation", "version": skill_version}, "status": "new",
                      "sources": [], "raw_transcriptions": [], "items": [], "groups": []}


def transcribe(path: Path, command: str, config_path: Path | None = None, timeout: float = 300) -> tuple[int, dict, str]:
    arguments = [command, str(path)]
    if config_path:
        interpreter = read_json(config_path).get("python")
        if interpreter:
            arguments.insert(0, interpreter)
        arguments.extend(["--config", str(config_path)])
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout)
        try:
            payload = json.loads(result.stdout)
            if not isinstance(payload, dict):
                raise ValueError("transcriber must return a JSON object")
        except ValueError:
            payload = {"error": {"kind": "invalid_output", "message": (result.stderr or result.stdout or "invalid transcriber output")[-1500:], "retryable": True}}
        return result.returncode, payload, result.stderr.strip()[-1500:]
    except subprocess.TimeoutExpired as error:
        return 1, {"error": {"kind": "timeout", "message": str(error), "retryable": True}}, ""
    except OSError as error:
        return 1, {"error": {"kind": "engine_unavailable", "message": str(error), "retryable": True}}, ""


def attempts(raw_dir: Path, recording_id: str) -> list[tuple[Path, dict]]:
    if not raw_dir.exists():
        return []
    found = []
    expression = re.compile(re.escape(recording_id) + r"\.attempt-(\d+)\.json$")
    for path in raw_dir.iterdir():
        match = expression.fullmatch(path.name)
        if match:
            found.append((int(match[1]), path, read_json(path)))
    return [(p, r) for _, p, r in sorted(found)]


def save_attempt(raw_dir: Path, recording_id: str, record: dict) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    prior = attempts(raw_dir, recording_id)
    number = max((int(re.search(r"attempt-(\d+)\.json$", p.name)[1]) for p, _ in prior), default=0) + 1
    target = raw_dir / f"{recording_id}.attempt-{number:02d}.json"
    # Stage a complete response, then publish using an exclusive link; interrupted
    # staging cannot create a half-written historical JSON or overwrite a name.
    temporary = raw_dir / f".{target.name}.{os.getpid()}.tmp"
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.link(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def retry_allowed(prior: list[tuple[Path, dict]], identity: str, force: bool) -> bool:
    if force:
        return True
    matching = [record for _, record in prior if record.get("stage", "primary") == "primary" and record.get("recognition_identity") == identity]
    if not matching:
        return True
    last = matching[-1]
    error = last.get("response", {}).get("error") or last.get("error", {})
    deterministic = not isinstance(error, dict) or not error.get("retryable", False)
    maximum = 2 if deterministic else 3
    if len(matching) >= maximum:
        return False
    stamp = last.get("finished_at_utc")
    if stamp:
        date = dt.datetime.fromisoformat(stamp).astimezone(ZoneInfo("Asia/Shanghai")).date()
        if date >= dt.datetime.now(ZoneInfo("Asia/Shanghai")).date():
            return False
    return True


def persist_batch(manifest: Path, batch: dict, before_hash: str | None, inbox: Path, preimage_dir: Path | None) -> None:
    if preimage_dir and manifest.exists():
        saved = preimage_dir / manifest.relative_to(inbox)
        if not saved.exists():
            saved.parent.mkdir(parents=True, exist_ok=True)
            with saved.open("xb") as stream:
                stream.write(manifest.read_bytes())
    manifest.parent.mkdir(parents=True, exist_ok=True)
    for name in ["raw", "review", "handoff"]:
        (manifest.parent / name).mkdir(exist_ok=True)
    write_json(manifest, batch, expected_hash=before_hash)


def run(args: argparse.Namespace, entries: list[Path], config: dict) -> dict:
    inbox = args.inbox_directory.expanduser().resolve()
    batches = load_batches(inbox)
    index = index_sources(batches)
    batch_id = args.batch_id or dt.datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    if Path(batch_id).name != batch_id or batch_id in [".", ".."]:
        raise ValueError("batch-id must be one directory name")
    daily_manifest, daily_batch = create_batch(inbox, batch_id, args.skill_version)
    loaded = {path: batch for path, batch in batches}
    loaded.setdefault(daily_manifest, daily_batch)
    hashes = {p: sha256(p) for p in loaded if p.exists()}
    summary = {"batch_id": batch_id, "discovered": 0, "already_processed": 0, "transcribed": 0, "failed": 0, "retried": 0}
    identity = object_hash({"executable": args.transcriber, "executable_sha256": sha256(Path(args.transcriber)) if args.transcriber else None, "config": config})
    deadline = time.monotonic() + float(config.get("run_budget_seconds", args.run_budget_seconds))
    for path in sorted(p for p in entries if p.name.lower().endswith(".m4a")):
        if not path.is_file():
            continue
        summary["discovered"] += 1
        if time.monotonic() >= deadline:
            summary['budget_deferred'] = summary.get('budget_deferred', 0) + 1
            continue
        try:
            digest = audio_sha256(path, float(config.get('audio_read_timeout_seconds', 15)))
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            summary.setdefault('unreadable_sources', []).append({'path': str(path), 'reason': str(error)[-600:]})
            continue
        existing = index.get(digest)
        if existing and existing[2] not in ["failed", "discovered"]:
            target, source, _ = existing
            old_path = source.get("current_path") or source.get("source_path")
            if old_path and not Path(old_path).is_file():
                source["current_path"] = str(path)
                persist_batch(target, loaded[target], hashes.get(target), inbox, args.preimage_directory)
                hashes[target] = sha256(target)
                summary["path_updated"] = summary.get("path_updated", 0) + 1
            summary["already_processed"] += 1
            continue
        if existing and existing[2] == "discovered" and not args.transcriber:
            summary["already_processed"] += 1
            continue
        target = existing[0] if existing else daily_manifest
        batch = loaded[target]
        raw_dir = target.parent / "raw"
        rid = str(existing[1]["id"]) if existing else "rec-" + digest[:12]
        source = next((v for v in batch["sources"] if str(v["id"]) == rid), None)
        prior = attempts(raw_dir, rid)
        recovered = next((r for _, r in reversed(prior) if r.get("source_sha256") == digest and r.get("recognition_identity") == identity and r.get("status") == "success"), None)
        force = bool(args.retry_failed and (rid in args.retry_failed or digest in args.retry_failed))
        if existing and existing[2] == "failed" and not recovered and not retry_allowed(prior, identity, force):
            summary["retry_deferred"] = summary.get("retry_deferred", 0) + 1
            continue
        timeout = 300.0
        decode_error = None
        if args.transcriber_config:
            try:
                secs = duration(path, config.get("ffprobe") or "/opt/homebrew/bin/ffprobe")
                timeout = max(float(config.get("timeout_minimum_seconds", 300)), 60 + float(config.get("timeout_duration_multiplier", 10)) * secs) + 60
            except RecognitionError as error:
                decode_error = {"error": {"kind": error.kind, "message": str(error), "retryable": error.retryable}}
        if args.transcriber and not recovered and time.monotonic() + timeout > deadline:
            summary["budget_deferred"] = summary.get("budget_deferred", 0) + 1
            continue
        if source is None:
            source = {"id": rid, "source_name": path.name, "source_path": str(path), "sha256": digest, "bytes": path.stat().st_size, "status": "discovered"}
            batch["sources"].append(source)
            if batch.get("status") in ["reviewed", "grouped", "handed_off"]:
                batch["status"] = "needs_human_review"
        if not args.transcriber:
            source["status"] = "discovered"
        else:
            if existing:
                summary["retried"] += 1
            if recovered:
                record = recovered
                summary["recovered"] = summary.get("recovered", 0) + 1
            else:
                started = now()
                code, payload, stderr = (1, decode_error, "") if decode_error else transcribe(path, args.transcriber, args.transcriber_config, timeout)
                success = code == 0 and isinstance(payload.get("text"), str) and bool(payload["text"].strip())
                if success and args.transcriber_config and payload.get("warnings"):
                    # A suspicious primary result gets one different-prompt check. Keep
                    # both hypotheses; neither replaces human evidence or raw history.
                    check_config = dict(config)
                    check_config.update({"personalization_enabled": False, "prompt_terms": [], "prompt": ""})
                    check_config.pop("profile_path", None)
                    if time.monotonic() + timeout <= deadline:
                        try:
                            check = recognize(path, check_config)
                        except RecognitionError as error:
                            check = {"text": "", "error": {"kind": error.kind, "message": str(error), "retryable": error.retryable}}
                        verification = {"id": rid, "source_sha256": digest, "stage": "verification", "recognition_identity": object_hash(check_config), "engine": args.transcriber,
                                        "configuration": check_config, "started_at_utc": started, "finished_at_utc": now(), "status": "success" if check.get("text") and not check.get("error") else "failed", "response": check}
                        verified_path = save_attempt(raw_dir, rid, verification)
                        payload["verification_attempt_path"] = str(verified_path)
                    payload["error"] = {"kind": "uncertain_transcript", "message": "识别证据不足或疑似模板内容；保留识别与复核响应，未作为灵感入库。", "retryable": False}
                    success = False
                if not success and not payload.get("error"):
                    payload["error"] = {"kind": "empty_transcript" if code == 0 else "engine_failed", "message": "empty text" if code == 0 else f"exit code {code}", "retryable": code != 0}
                record = {"id": rid, "source_sha256": digest, "engine": args.transcriber, "recognition_identity": identity,
                          "configuration": config, "started_at_utc": started, "finished_at_utc": now(), "status": "success" if success else "failed", "response": payload}
                if stderr:
                    record["stderr"] = stderr
                saved = save_attempt(raw_dir, rid, record)
                record["attempt_path"] = str(saved)
            raw_by_id = {str(v["id"]): v for v in batch.get("raw_transcriptions", [])}
            raw_by_id[rid] = record
            batch["raw_transcriptions"] = list(raw_by_id.values())
            good = record["status"] == "success"
            source["status"] = "recognized" if good else "failed"
            source["recognition_identity"] = identity
            if good:
                summary["transcribed"] += 1
                if batch.get("status") in ["new", "recognized"]:
                    batch["status"] = "needs_human_review"
            else:
                summary["failed"] += 1
        persist_batch(target, batch, hashes.get(target), inbox, args.preimage_directory)
        hashes[target] = sha256(target)
        index[digest] = (target, source, source["status"])
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_directory", type=Path)
    parser.add_argument("inbox_directory", type=Path)
    parser.add_argument("--transcriber")
    parser.add_argument("--transcriber-config", type=Path)
    parser.add_argument("--batch-id")
    parser.add_argument("--skill-version", default="unknown")
    parser.add_argument("--run-budget-seconds", type=float, default=1800)
    parser.add_argument("--retry-failed", action="append", metavar="RECORDING_ID_OR_SHA256")
    parser.add_argument("--preimage-directory", type=Path)
    args = parser.parse_args()
    source = args.source_directory.expanduser().resolve()
    if not source.is_dir():
        parser.error(f"source directory does not exist or is inaccessible: {source}")
    try:
        entries = list(source.iterdir())
    except OSError as error:
        parser.error(f"cannot list source directory {source}: {error}")
    if args.transcriber_config and not args.transcriber:
        parser.error("--transcriber-config requires --transcriber")
    try:
        config = effective_config(read_json(args.transcriber_config)) if args.transcriber_config else {}
        with file_lock(args.inbox_directory.resolve() / ".intake.lock"):
            summary = run(args, entries, config)
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
