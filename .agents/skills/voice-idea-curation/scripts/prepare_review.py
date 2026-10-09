#!/usr/bin/env python3
"""Render a compact exception review; preserve human confirmation and old versions."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import shutil

from asr_support import file_lock, now, object_hash, read_json, sha256, write_json


def prepare(batch: dict, curation: dict) -> tuple[dict, str]:
    review = copy.deepcopy(batch.get("review", {"version": 0, "status": "pending_human_review", "recordings": []}))
    old = {str(r["recording_id"]): r for r in review.get("recordings", [])}
    raw = {str(r["id"]): r for r in batch.get("raw_transcriptions", [])}
    proposals = {str(r["recording_id"]): r for r in curation.get("recordings", [])}
    recorded = []
    for source in batch.get("sources", []):
        rid = str(source["id"])
        response = raw.get(rid, {}).get("response", {})
        accepted = raw.get(rid, {}).get("status") == "success" and not response.get("error")
        if (not accepted or not response.get("text")) and rid not in old:
            continue
        entry = copy.deepcopy(old.get(rid, {"review_id": f"R{len(recorded)+1:02d}", "recording_id": rid,
            "source_sha256": source.get("sha256") or source.get("source_sha256"), "status": "pending_human_review",
            "human_confirmed_text": None, "confirmation_evidence": None, "uncertainties": []}))
        proposal = proposals.get(rid)
        if proposal:
            if any(k in proposal for k in ["human_confirmed_text", "confirmation_evidence", "status"]):
                raise ValueError("curation cannot supply human confirmation fields")
            unresolved_before = entry.get("uncertainties", [])
            unresolved_after = proposal.get("uncertainties", unresolved_before)
            if unresolved_before and unresolved_after != unresolved_before and not proposal.get("resolution_evidence"):
                raise ValueError(f"existing uncertainty changed without resolution evidence: {rid}")
            for path in proposal.get("supporting_attempt_paths", []):
                support = read_json(Path(path))
                support = support.get("response", support)
                if support.get("path") and Path(support["path"]).name != Path(source.get("source_path", "")).name:
                    raise ValueError("supporting attempt refers to another recording")
            entry["ai_draft"] = proposal.get("ai_draft", entry.get("ai_draft", response.get("text", "")))
            entry["summary"] = proposal.get("summary", entry.get("summary", ""))
            entry["uncertainties"] = unresolved_after
            entry["machine_review"] = {"status": "machine_checked", "supporting_attempt_paths": proposal.get("supporting_attempt_paths", []), "resolution_evidence": proposal.get("resolution_evidence", []), "note": "机器整理与复核，不等同人工确认。"}
        else:
            entry.setdefault("ai_draft", response.get("text", ""))
            entry.setdefault("summary", "")
            entry.setdefault("machine_review", {"status": "raw_only", "note": "尚待当前任务进行语义整理。"})
        entry["raw_text"] = response.get("text", "")
        entry["source_path"] = source.get("current_path") or source.get("source_path")
        entry["attempt_path"] = raw.get(rid, {}).get("attempt_path")
        entry["config_sha256"] = response.get("config_sha256")
        for warning in response.get("warnings", []):
            if warning not in entry["uncertainties"]:
                entry["uncertainties"].append(warning)
        recorded.append(entry)
    review["recordings"] = recorded
    review["transcript_comparison_path"] = "review/转写对照稿.md"
    pending = [r for r in recorded if not r.get("confirmation_evidence")]
    review["status"] = "pending_human_review" if pending else "reviewed"
    lines = [f"# 转写对照稿 · {batch['batch_id']}", "", "先看摘要和疑点；无需逐句校对。机器整理不代表人工确认，确认原意后才进入分类交接。", "", "## 内容摘要", ""]
    for entry in recorded:
        description = entry.get("summary") or entry.get("ai_draft") or "（未恢复）"
        flag = "已有人工作为依据" if entry.get("confirmation_evidence") else "待原意确认"
        lines.append(f"- **{entry['review_id']}**：{description}（{flag}）")
    lines.extend(["", "## 需要澄清的疑点", ""])
    count = 0
    for entry in pending:
        for question in entry.get("uncertainties", []):
            count += 1
            if isinstance(question, str):
                detail, window = question, "整段"
            else:
                detail = question.get("detail") or question.get("message") or question.get("question") or str(question)
                span = question.get("span")
                if not span and question.get("start_seconds") is not None:
                    span = {"start": question["start_seconds"], "end": question["end_seconds"]}
                window = question.get("window") or (f"{span['start']:.2f}–{span['end']:.2f}秒" if span else "整段")
            lines.append(f"- **{entry['review_id']} · {window}**：{detail}")
    if not count:
        lines.append("本轮没有另列的实质疑点。你可以统一确认摘要是否表达原意；这不表示已有完整逐字准确率验证。")
    failures = [s for s in batch.get("sources", []) if s.get("status") == "failed"]
    if failures:
        lines.extend(["", "## 未完成识别", ""])
        for source in failures:
            error = raw.get(str(source["id"]), {}).get("response", {}).get("error", "空文本或识别失败")
            lines.append(f"- {source['id']}：{error}")
    lines.extend(["", "## 原始识别与整理稿", ""])
    for entry in recorded:
        lines.extend([f"### {entry['review_id']} · {entry['recording_id']}", "", f"[原录音](<{entry['source_path']}>)", "", "**原始识别**", "", entry["raw_text"] or "（空）", "", "**AI 整理稿**", "", entry["ai_draft"], ""])
        if entry.get("machine_review", {}).get("supporting_attempt_paths"):
            lines.append("复核来源：" + "、".join(f"[识别记录](<{p}>)" for p in entry["machine_review"]["supporting_attempt_paths"]))
            lines.append("")
        if entry.get("confirmation_evidence"):
            lines.extend(["**人工确认**", "", entry.get("human_confirmed_text") or "已确认条目原意；未标为逐字真值。", ""])
    review["draft_digest"] = object_hash({"recordings": recorded, "failures": failures})
    return review, "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inbox", type=Path, required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--curation", type=Path)
    parser.add_argument("--preview-only", action="store_true")
    parser.add_argument("--preimage-directory", type=Path)
    args = parser.parse_args()
    if Path(args.batch_id).name != args.batch_id:
        parser.error("invalid batch-id")
    try:
        with file_lock(args.inbox / ".intake.lock"):
            manifest = args.inbox / "batches" / args.batch_id / "batch.json"
            before = sha256(manifest)
            batch = read_json(manifest)
            curation = read_json(args.curation) if args.curation else {}
            review, markdown = prepare(batch, curation)
            target = manifest.parent / "review/转写对照稿.md"
            result = {"batch_id": args.batch_id, "review_path": str(target), "recordings": len(review["recordings"]), "pending": sum(not bool(r.get("confirmation_evidence")) for r in review["recordings"]), "uncertainties": sum(len(r.get("uncertainties", [])) for r in review["recordings"])}
            if args.preview_only:
                print(json.dumps({**result, "status": "preview", "draft": review}, ensure_ascii=False))
                return 0
            if review.get("draft_digest") == batch.get("review", {}).get("draft_digest") and target.exists():
                print(json.dumps({**result, "status": "unchanged"}, ensure_ascii=False))
                return 0
            previous = int(batch.get("review", {}).get("version", 0))
            version_dir = target.parent / "versions" / f"{previous:04d}"
            version_dir.mkdir(parents=True, exist_ok=True)
            if target.exists() and not (version_dir / target.name).exists():
                shutil.copyfile(target, version_dir / target.name)
            if batch.get("review") and not (version_dir / "review.json").exists():
                write_json(version_dir / "review.json", batch["review"])
            if args.preimage_directory:
                for path in [manifest, target]:
                    saved = args.preimage_directory / path.relative_to(args.inbox)
                    if path.exists() and not saved.exists():
                        saved.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(path, saved)
            review.update({"version": previous + 1, "updated_at": now()})
            batch["review"] = review
            if any(not r.get("confirmation_evidence") for r in review["recordings"]):
                batch["status"] = "needs_human_review"
            write_json(manifest, batch, expected_hash=before)
            temporary = target.with_suffix(".md.tmp")
            temporary.write_text(markdown, encoding="utf-8")
            temporary.replace(target)
            print(json.dumps({**result, "status": "written", "version": review["version"]}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
