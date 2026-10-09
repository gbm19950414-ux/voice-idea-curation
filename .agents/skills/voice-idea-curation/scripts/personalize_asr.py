#!/usr/bin/env python3
"""Persist evidence-linked human corrections; model drafts cannot be imported as gold."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from asr_support import active_corrections, file_lock, now, object_hash, profile_data, read_json, sha256, write_json


def evidence_text(reference: str) -> str:
    filename, separator, locator = reference.partition("#")
    if not separator:
        raise ValueError("confirmation_ref requires a JSON evidence locator")
    evidence = read_json(Path(filename))
    collection, _, identifier = locator.partition("/")
    if collection != "human_review_evidence":
        raise ValueError("use a saved human_review_evidence record, not an AI transcript")
    record = next((v for v in evidence.get(collection, []) if v.get("id") == identifier), None)
    if not record or not str(record.get("kind", "")).startswith("user_"):
        raise ValueError("confirmation is not a saved user evidence record")
    return record["text"]


def validate_correction(value: dict) -> None:
    for key in ["id", "recording_id", "source_sha256", "manifest_path", "confirmation_ref", "evidence_kind"]:
        if not value.get(key):
            raise ValueError(f"missing correction field: {key}")
    manifest = read_json(Path(value["manifest_path"]))
    source = next((s for s in manifest.get("sources", []) if str(s.get("id")) == value["recording_id"]), None)
    if not source or (source.get("sha256") or source.get("source_sha256")) != value["source_sha256"]:
        raise ValueError("correction recording identity does not match manifest")
    text = evidence_text(value["confirmation_ref"])
    if value["evidence_kind"] not in ["explicit_word_correction", "semantic_clarification", "revocation"]:
        raise ValueError("machine suggestions cannot become human corrections")
    if not value.get("revokes"):
        if not value.get("observed") or not value.get("confirmed"):
            raise ValueError("observed and confirmed wording are required")
        if value["confirmed"].casefold() not in text.casefold():
            raise ValueError("confirmed wording is not present in referenced user evidence")


def validate_profile(path: Path) -> dict:
    profile, corrections = profile_data(path)
    ids = set()
    for value in corrections:
        validate_correction(value)
        if value["id"] in ids:
            raise ValueError("duplicate correction ID")
        if value.get("revokes") and value["revokes"] not in ids:
            raise ValueError("revocation does not refer to an earlier correction")
        if value.get("supersedes") and value["supersedes"] not in ids:
            raise ValueError("supersedes does not refer to an earlier correction")
        ids.add(value["id"])
    event_revision = max((v.get("revision", 0) for v in corrections), default=0)
    if profile["revision"] != event_revision:
        raise ValueError("profile revision and correction log disagree; interrupted import needs inspection")
    return {"status": "valid", "revision": profile["revision"], "corrections": len(corrections), "active": len(active_corrections(corrections)), "sha256": object_hash({"profile": profile, "corrections": corrections})}


def add(path: Path, confirmation: Path) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path.parent / ".profile.lock"):
        if not path.exists():
            log = path.parent / "corrections.jsonl"
            if log.exists() and log.stat().st_size:
                raise ValueError("existing correction log has no profile; inspect instead of overwriting")
            log.touch(exist_ok=True)
            write_json(path, {"schema_version": 1, "profile_id": "personal-asr", "revision": 0, "corrections_path": str(log.resolve()), "project_term_refs": [], "created_at": now(), "updated_at": now()})
        validate_profile(path)
        profile, corrections = profile_data(path)
        value = read_json(confirmation)
        validate_correction(value)
        previous = next((v for v in corrections if v["id"] == value["id"]), None)
        if previous:
            clean = {k: v for k, v in previous.items() if k not in ["revision", "imported_at"]}
            if clean != value:
                raise ValueError("correction ID already contains different content")
            return {"status": "already_imported", "id": value["id"]}
        revision = profile["revision"] + 1
        value.update({"revision": revision, "imported_at": now()})
        versions = path.parent / "versions"
        versions.mkdir(exist_ok=True)
        old_revision = profile["revision"]
        snapshot = versions / f"profile-{old_revision:04d}.json"
        if not snapshot.exists():
            write_json(snapshot, {"profile": profile, "corrections": corrections})
        log = Path(profile["corrections_path"])
        before = sha256(log)
        data = log.read_text() + json.dumps(value, ensure_ascii=False) + "\n"
        temporary = log.with_suffix(".jsonl.tmp")
        temporary.write_text(data)
        if sha256(log) != before:
            temporary.unlink()
            raise RuntimeError("correction log changed during import")
        os.replace(temporary, log)
        profile.update({"revision": revision, "updated_at": now()})
        write_json(path, profile)
        return validate_profile(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["validate", "add"])
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--confirmation", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "add" and not args.confirmation:
            parser.error("add requires --confirmation")
        result = add(args.profile, args.confirmation) if args.action == "add" else validate_profile(args.profile)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, RuntimeError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
