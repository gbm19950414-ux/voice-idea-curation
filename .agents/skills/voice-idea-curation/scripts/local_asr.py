#!/usr/bin/env python3
"""Recognize one local audio file, preserving raw model output and timing."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import subprocess
import tempfile
import time
import wave

from asr_support import effective_config, object_hash, read_json


class RecognitionError(Exception):
    def __init__(self, kind: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.kind, self.retryable = kind, retryable


def duration(path: Path, ffprobe: str) -> float:
    try:
        response = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)], capture_output=True, text=True, timeout=30, check=True)
        return float(response.stdout.strip())
    except (ValueError, subprocess.SubprocessError, OSError) as error:
        raise RecognitionError("decode_failed", str(error)) from error


def normalize_whisper(raw: dict) -> tuple[str, list[dict]]:
    if not isinstance(raw.get("transcription"), list):
        raise RecognitionError("invalid_output", "whisper JSON has no transcription list")
    segments = []
    for entry in raw["transcription"]:
        offsets = entry.get("offsets", {})
        segments.append({"text": entry.get("text", "").strip(), "start": offsets.get("from", 0) / 1000,
                         "end": offsets.get("to", 0) / 1000, "tokens": entry.get("tokens", [])})
    return "".join(v["text"] for v in segments).strip(), segments


def quality_warnings(text: str, segments: list[dict]) -> list[dict]:
    """Route weak evidence for rechecking, never treat model probabilities as accuracy."""
    import re
    warnings = []
    letters = re.sub(r"[\W_]+", "", text)
    if text and len(letters) <= 3:
        warnings.append({"kind": "insufficient_context", "message": "识别内容过短，无法可靠确定独立灵感；需要复核。"})
    probabilities = [t["p"] for s in segments for t in s.get("tokens", []) if t.get("p") is not None and not t.get("text", "").startswith("[_")]
    if probabilities and sum(math.log(max(p, 1e-9)) for p in probabilities) / len(probabilities) < -1.2:
        warnings.append({"kind": "weak_decoder_evidence", "message": "本模型解码证据较弱；此阈值只用于复核路由，不是准确率。"})
    if any(marker in text for marker in ["感谢观看", "謝謝觀看", "点赞", "點讚", "MING PAO", "Television Series Exclusive"]):
        warnings.append({"kind": "possible_boilerplate", "message": "可能为背景声或模板误识别；保留原始响应，复核后再使用。"})
    return warnings


def recognize(path: Path, config: dict) -> dict:
    start = time.monotonic()
    config = effective_config(config)
    secs = duration(path, config.get("ffprobe") or "/opt/homebrew/bin/ffprobe")
    timeout = max(float(config.get("timeout_minimum_seconds", 300)), 60 + float(config.get("timeout_duration_multiplier", 10)) * secs)
    prompt = config.get("prompt", "")
    if config.get("prompt_terms"):
        prompt += " " + "、".join(config["prompt_terms"])[:600] + "。"
    metadata = {"schema_version": 1, "path": str(path), "engine": config["engine"],
                "model_revision": config.get("model_revision"), "model_sha256": config.get("model_sha256"),
                "engine_commit": config.get("engine_commit"), "config_sha256": object_hash(config),
                "profile_version": config.get("profile_version"), "profile_sha256": config.get("profile_sha256"),
                "duration_seconds": secs, "prompt_terms": config.get("prompt_terms", []), "warnings": []}
    metadata["vad"] = {"model_sha256": config.get("vad_sha256"), "threshold": config.get("vad_threshold")} if config.get("vad_model_path") else None
    try:
        with tempfile.TemporaryDirectory(prefix="voice-asr-") as temporary:
            wav = Path(temporary) / "audio.wav"
            output = Path(temporary) / "recognized"
            converted = subprocess.run([config.get("ffmpeg") or "/opt/homebrew/bin/ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)], capture_output=True, text=True, timeout=60)
            if converted.returncode:
                raise RecognitionError("decode_failed", converted.stderr[-1500:])
            with wave.open(str(wav)) as audio:
                frames = audio.readframes(audio.getnframes())
                # Only digital near-silence is rejected: quiet human speech is retained.
                import array
                samples = array.array("h", frames)
                peak = max((abs(v) for v in samples), default=0)
            if peak <= 3:
                raise RecognitionError("empty_transcript", "digital silence; no model-generated text accepted")
            if config["engine"] != "whisper-cpp":
                raise RecognitionError("engine_unavailable", "unsupported configured engine")
            command = [config["binary"], "-m", config["model_path"], "-f", str(wav), "-l", config.get("language", "zh"),
                       "-t", str(config.get("threads", 6)), "-bs", str(config.get("beam_size", 5)), "-ojf", "-of", str(output), "-nf"]
            if prompt:
                command.extend(["--prompt", prompt])
            if config.get("use_gpu") is False:
                command.append("-ng")
            if config.get("vad_model_path"):
                command.extend(["--vad", "-vm", config["vad_model_path"], "-vt", str(config.get("vad_threshold", 0.25)), "-vspd", "150", "-vp", "100"])
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
            if result.returncode:
                raise RecognitionError("engine_failed", result.stderr[-1500:], True)
            try:
                raw = read_json(output.with_suffix(".json"))
            except ValueError as error:
                raise RecognitionError("invalid_output", str(error), True) from error
            text, segments = normalize_whisper(raw)
            metadata.update({"text": text, "segments": segments, "raw_engine_response": raw,
                             "engine_stderr": result.stderr[-6000:], "elapsed_seconds": time.monotonic() - start})
            metadata["warnings"].extend(quality_warnings(text, segments))
            if not text:
                metadata["error"] = {"kind": "empty_transcript", "message": "recognizer returned empty text", "retryable": False}
            import re
            clean = lambda value: re.sub(r"[\W_]+", "", value).casefold()
            if text and prompt.strip() and clean(text) == clean(prompt):
                metadata["error"] = {"kind": "prompt_echo", "message": "output reproduces only the supplied context; not accepted as an idea", "retryable": False}
            if text and len(text) > 24 and len(set(text)) < len(text) / 8:
                metadata["warnings"].append({"kind": "repetition", "message": "repetitive text requires review"})
            return metadata
    except subprocess.TimeoutExpired as error:
        raise RecognitionError("timeout", str(error), True) from error
    except FileNotFoundError as error:
        raise RecognitionError("engine_unavailable", str(error), True) from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        response = recognize(args.audio.resolve(), read_json(args.config))
        code = 0 if response.get("text") and not response.get("error") else 1
    except RecognitionError as error:
        response = {"path": str(args.audio), "text": "", "segments": [], "error": {"kind": error.kind, "message": str(error), "retryable": error.retryable}}
        code = 1
    except (ValueError, OSError, KeyError) as error:
        response = {"path": str(args.audio), "text": "", "segments": [], "error": {"kind": "configuration_error", "message": str(error), "retryable": False}}
        code = 1
    print(json.dumps(response, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
