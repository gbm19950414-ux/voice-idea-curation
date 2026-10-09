"""Shared persistence and provenance helpers for local voice curation."""
from __future__ import annotations

from contextlib import contextmanager
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("expected JSON object")
        return value
    except (OSError, ValueError) as error:
        raise ValueError(f"cannot read {path}: {error}") from error


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def object_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def write_json(path: Path, value: dict, *, expected_hash: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if expected_hash is not None and (not path.exists() or sha256(path) != expected_hash):
            raise RuntimeError(f"preimage changed while processing: {path}")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(f"another process is writing: {path}") from error
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def profile_data(path: Path) -> tuple[dict, list[dict]]:
    profile = read_json(path)
    if profile.get("schema_version") != 1:
        raise ValueError(f"unsupported profile schema: {path}")
    corrections_path = Path(profile["corrections_path"])
    corrections = []
    for number, line in enumerate(corrections_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"invalid correction at {corrections_path}:{number}")
        corrections.append(value)
    return profile, corrections


def active_corrections(corrections: list[dict]) -> list[dict]:
    removed = {v.get("revokes") for v in corrections if v.get("revokes")}
    removed.update(v.get("supersedes") for v in corrections if v.get("supersedes"))
    return [v for v in corrections if v["id"] not in removed and not v.get("revokes") and not v.get("revoked")]


def effective_config(config: dict) -> dict:
    result = dict(config)
    terms = list(result.get("prompt_terms", []))
    if result.get("personalization_enabled") and result.get("profile_path"):
        profile, corrections = profile_data(Path(result["profile_path"]))
        result["profile_version"] = profile["revision"]
        result["profile_sha256"] = object_hash({"profile": profile, "corrections": corrections})
        for value in active_corrections(corrections):
            if value.get("evidence_kind") == "explicit_word_correction" and value.get("scope", "personal") in ["personal", result.get("project_id")]:
                terms.append(value["confirmed"])
        refs = []
        for ref in profile.get("project_term_refs", []):
            if ref.get("project_id") != result.get("project_id"):
                continue
            path = Path(ref["path"])
            if path.is_file():
                # The project owns the glossary. Consume declared terms, never copy its architecture.
                glossary = read_json(path) if path.suffix == ".json" else {"terms": [v.strip() for v in path.read_text().splitlines() if v.strip() and not v.startswith("#")]}
                terms.extend(glossary.get("terms", []))
                refs.append({"path": str(path), "sha256": sha256(path)})
        result["project_term_provenance"] = refs
    result["prompt_terms"] = list(dict.fromkeys(str(v) for v in terms))[:40]
    result["prompt_terms"] = [v for v in result["prompt_terms"] if len(v) <= 80]
    return result
