#!/usr/bin/env python3
"""Run frozen audio comparisons outside production batches. No full-gold WER claim."""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import time

from asr_support import object_hash, read_json, sha256, write_json
from local_asr import RecognitionError, recognize


def score_anchor(response: dict, label: dict) -> dict:
    text = response.get("text", "")
    window = label.get("span")
    if window and response.get("segments"):
        near = [s["text"] for s in response["segments"] if s.get("end", 0) >= window["start"] - 1.5 and s.get("start", 0) <= window["end"] + 1.5]
        if near:
            text = "".join(near)
    # This is a word anchor proxy, not a verbatim or overall accuracy score.
    expected = label["expected"]
    found = expected.casefold() in text.casefold()
    if label["observed"] != "还":
        found = found and label["observed"] not in text
    return {"expected": expected, "observed": label["observed"], "found": found,
            "evaluated_text": text, "method": "nearby-segment anchor presence; no full-gold WER"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", type=Path)
    args = parser.parse_args()
    corpus, config = read_json(args.corpus), read_json(args.config)
    run = args.resume or args.output / dt.datetime.now().strftime("run-%Y%m%dT%H%M%S%f")
    run.mkdir(parents=True, exist_ok=True)
    fingerprint = {"corpus_sha256": sha256(args.corpus), "config_sha256": object_hash(config), "adapter_sha256": sha256(Path(__file__).with_name("local_asr.py"))}
    result_path = run / "results.json"
    results = read_json(result_path) if result_path.exists() else {"schema_version": 1, "fingerprint": fingerprint, "started_at": dt.datetime.now(dt.timezone.utc).isoformat(), "results": []}
    if results["fingerprint"] != fingerprint:
        raise ValueError("resume fingerprint mismatch")
    done = {(r["source_sha256"], r["variant"]) for r in results["results"]}
    for entry in corpus["recordings"]:
        if not entry["available"]:
            continue
        path = Path(entry["path"])
        if sha256(path) != entry["source_sha256"]:
            raise ValueError(f"source hash changed: {path}")
        for variant in ["no_personalization", "confirmed_terms"]:
            if (entry["source_sha256"], variant) in done:
                continue
            settings = dict(config)
            settings["personalization_enabled"] = False
            settings.pop("profile_path", None)
            settings["prompt_terms"] = corpus["prompt_terms"] if variant == "confirmed_terms" else []
            start = time.monotonic()
            try:
                response = recognize(path, settings)
            except RecognitionError as error:
                response = {"text": "", "segments": [], "error": {"kind": error.kind, "message": str(error), "retryable": error.retryable}}
            raw_path = run / (entry["source_sha256"][:12] + "." + variant + ".json")
            write_json(raw_path, response)
            baseline = entry["baseline"].get("response", {})
            record = {"recording_id": entry["recording_id"], "batch_id": entry["batch_id"], "source_sha256": entry["source_sha256"],
                      "path": entry["path"], "role": entry["role"], "variant": variant, "response_path": str(raw_path),
                      "text": response.get("text", ""), "baseline_text": baseline.get("text", ""),
                      "error": response.get("error"), "elapsed_seconds": time.monotonic() - start,
                      "duration_seconds": entry.get("duration_seconds"),
                      "anchors": [score_anchor(response, v) for v in entry["labels"]],
                      "baseline_anchors": [score_anchor(baseline, v) for v in entry["labels"]],
                      "unresolved_spans": response.get("warnings", [])}
            results["results"].append(record)
            write_json(result_path, results)
            print(json.dumps({"recording_id": entry["recording_id"], "variant": variant, "elapsed": round(record["elapsed_seconds"], 2), "text": record["text"], "anchors": record["anchors"], "error": record["error"]}, ensure_ascii=False), flush=True)
    results["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    results["summary"] = {"comparisons": len(results["results"]), "nonempty": sum(bool(r["text"]) for r in results["results"]), "elapsed_seconds": sum(r["elapsed_seconds"] for r in results["results"]), "limitation": "sparse confirmed anchors, no overall accuracy percentage"}
    write_json(result_path, results)
    lines = ["# 本地 ASR 同源比较", "", "只比较明确词语锚点；非空结果不是准确度证明，未使用整理稿作为逐字真值。", ""]
    for r in results["results"]:
        lines.extend([f"## {r['batch_id']} / {r['recording_id']} / {r['variant']}", "", f"原识别：{r['baseline_text'] or '（空）'}", "", f"候选：{r['text'] or '（空）'}", "", f"耗时：{r['elapsed_seconds']:.2f}s；锚点：" + json.dumps(r["anchors"], ensure_ascii=False), ""])
    (run / "comparison.md").write_text("\n".join(lines))
    print(json.dumps({"run": str(run), "summary": results["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
