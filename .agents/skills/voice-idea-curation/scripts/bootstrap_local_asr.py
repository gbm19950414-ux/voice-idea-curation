#!/usr/bin/env python3
"""Build a pinned, isolated local recognizer. Never change the production config."""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import venv


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "voice-idea-curation"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    os.replace(temp, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=["whisper-cpp", "funasr"], required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--config-out", type=Path, required=True)
    parser.add_argument("--vad", action="store_true", help="Add the pinned official Silero speech detector after a demonstrated need.")
    args = parser.parse_args()
    root = args.runtime_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".bootstrap.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.engine != "whisper-cpp" or args.model != "large-v3":
            parser.error("alternative engine must be implemented only after evaluation establishes its need")
        lock_path = root / "runtime-lock.json"
        if lock_path.exists():
            record = json.loads(lock_path.read_text())
        else:
            release = fetch_json("https://api.github.com/repos/ggml-org/whisper.cpp/releases/latest")
            ref = fetch_json("https://api.github.com/repos/ggml-org/whisper.cpp/commits/" + release["tag_name"])
            model = fetch_json("https://huggingface.co/api/models/ggerganov/whisper.cpp?blobs=true")
            sibling = next(x for x in model["siblings"] if x["rfilename"] == "ggml-large-v3.bin")
            cmake = fetch_json("https://pypi.org/pypi/cmake/json")["info"]["version"]
            record = {"schema_version": 1, "engine": args.engine, "model": args.model,
                      "release": release["tag_name"], "commit": ref["sha"], "model_revision": model["sha"],
                      "model_expected_sha256": sibling["lfs"]["sha256"], "cmake_version": cmake,
                      "python": sys.executable, "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                      "license": "whisper.cpp MIT; model license from official model repository", "status": "pinned"}
            save(lock_path, record)
        environment = root / "build-venv"
        if not (environment / "bin/python").exists():
            venv.EnvBuilder(with_pip=True).create(environment)
        cmake = environment / "bin/cmake"
        if not cmake.exists():
            subprocess.run([str(environment / "bin/python"), "-m", "pip", "install", "cmake==" + record["cmake_version"]], check=True)
        checkout = root / "whisper.cpp"
        if not (checkout / ".git").exists():
            subprocess.run(["git", "clone", "--depth", "1", "--branch", record["release"], "https://github.com/ggml-org/whisper.cpp.git", str(checkout)], check=True)
        actual = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
        if actual != record["commit"]:
            raise RuntimeError("checkout differs from pinned commit; refusing to reset it")
        binary = checkout / "build/bin/whisper-cli"
        if not binary.exists():
            sdk = subprocess.check_output(["xcrun", "--show-sdk-path"], text=True).strip()
            headers = Path(sdk) / "usr/include/c++/v1"
            build_options = ["-DCMAKE_BUILD_TYPE=Release", "-DWHISPER_BUILD_TESTS=OFF", "-DGGML_METAL_EMBED_LIBRARY=ON", "-DCMAKE_OSX_SYSROOT=" + sdk]
            # Some CLT installs put libc++ in the SDK while clang searches the CLT root.
            # Scope the existing header path to this build; never repair system directories.
            if (headers / "array").exists():
                build_options.append("-DCMAKE_CXX_FLAGS=-isystem " + str(headers))
            record["build_options"] = build_options
            save(lock_path, record)
            subprocess.run([str(cmake), "-S", str(checkout), "-B", str(checkout / "build"),
                            *build_options], check=True)
            subprocess.run([str(cmake), "--build", str(checkout / "build"), "--config", "Release", "--target", "whisper-cli", "-j", "6"], check=True)
        weights = root / "models/ggml-large-v3.bin"
        weights.parent.mkdir(exist_ok=True)
        if not weights.exists():
            part = weights.with_suffix(".bin.part")
            url = "https://huggingface.co/ggerganov/whisper.cpp/resolve/" + record["model_revision"] + "/ggml-large-v3.bin"
            subprocess.run(["curl", "--fail", "--location", "--retry", "3", "--continue-at", "-", "--output", str(part), url], check=True)
            if file_hash(part) != record["model_expected_sha256"]:
                raise RuntimeError("model SHA-256 mismatch; partial file retained, production unchanged")
            os.replace(part, weights)
        model_hash = file_hash(weights)
        if model_hash != record["model_expected_sha256"]:
            raise RuntimeError("installed model SHA-256 mismatch")
        vad_options = {}
        if args.vad:
            if not record.get("vad"):
                vad_info = fetch_json("https://huggingface.co/api/models/ggml-org/whisper-vad?blobs=true")
                vad_file = next(x for x in vad_info["siblings"] if x["rfilename"] == "ggml-silero-v6.2.0.bin")
                record["vad"] = {"revision": vad_info["sha"], "sha256": vad_file["lfs"]["sha256"], "filename": vad_file["rfilename"]}
                save(lock_path, record)
            vad = record["vad"]
            vad_path = root / "models" / vad["filename"]
            if not vad_path.exists():
                part = vad_path.with_suffix(".bin.part")
                subprocess.run(["curl", "--fail", "--location", "--retry", "3", "--output", str(part), "https://huggingface.co/ggml-org/whisper-vad/resolve/" + vad["revision"] + "/" + vad["filename"]], check=True)
                if file_hash(part) != vad["sha256"]:
                    raise RuntimeError("VAD model SHA-256 mismatch")
                os.replace(part, vad_path)
            if file_hash(vad_path) != vad["sha256"]:
                raise RuntimeError("installed VAD model SHA-256 mismatch")
            vad_options = {"vad_model_path": str(vad_path), "vad_sha256": vad["sha256"], "vad_revision": vad["revision"], "vad_threshold": 0.25}
        record.update({"status": "ready", "binary": str(binary), "binary_sha256": file_hash(binary),
                       "model_path": str(weights), "model_sha256": model_hash,
                       "pip_freeze": subprocess.check_output([str(environment / "bin/python"), "-m", "pip", "freeze"], text=True).splitlines(),
                       "build_options": record.get("build_options", [])})
        save(lock_path, record)
        config = {"schema_version": 1, "engine": "whisper-cpp", "binary": str(binary), "model_path": str(weights),
                  "model_revision": record["model_revision"], "model_sha256": model_hash, "engine_commit": record["commit"],
                  "runtime_lock": str(lock_path), "python": sys.executable,
                  "ffmpeg": shutil.which("ffmpeg"), "ffprobe": shutil.which("ffprobe"),
                  "language": "zh", "threads": 6, "beam_size": 5, "prompt": "",
                  "prompt_terms": [], "personalization_enabled": False, "timeout_minimum_seconds": 300,
                  "timeout_duration_multiplier": 10, "run_budget_seconds": 1800, **vad_options}
        if args.config_out.exists() and json.loads(args.config_out.read_text()) != config:
            raise RuntimeError("candidate config exists with different settings; refusing to overwrite")
        save(args.config_out, config)
        print(json.dumps({"status": "ready", "release": record["release"], "config": str(args.config_out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
