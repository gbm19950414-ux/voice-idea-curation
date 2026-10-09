# Local recognition and personal correction

The reusable code owns inference and review mechanics. The inbox owns private profiles, corrections, evaluation material and batches. The project's own glossary remains project-owned. No transcription API or acoustic fine-tuning is required for this route.

## Daily intake

Call the scanner with the existing source/inbox paths and `--transcriber <absolute local_asr.py> --transcriber-config <absolute recognizer.active.json>`. The active JSON supplies pinned executable/model paths, Python, ffmpeg/ffprobe, language, decoding, optional VAD and the personal profile. Use the explicit interpreter in the configuration; do not rely on a venv activated in an interactive shell.

`local_asr.py AUDIO --config CONFIG` emits one JSON object retaining raw output, timing and optional warnings. It uses a temporary PCM conversion which is removed on exit. Silence, empty output and unsupported/weak evidence are not proof of missing speech. Decoder probability only routes further checking; it is not an accuracy percentage. Speech detection is deliberately conservative and may still mistake noise for speech.

The scanner uses audio hashes across all batches and a nonblocking inbox lock. Unreadable source directories/manifests fail explicitly. A successful historical source is not silently retranscribed on a model upgrade. Failed recognition stays in its original batch. Each effective model/config/profile identity has at most two deterministic or three transient automatic attempts, at most one per Shanghai day. `--retry-failed ID_OR_HASH` is a manually scoped override; do not use it routinely in the scheduled prompt. `--preimage-directory PATH` saves existing manifests before a planned migration. Run budget defaults to 30 minutes; deferred work remains eligible later.

## Review and selective verification

After intake, call `prepare_review.py --inbox INBOX --batch-id ID --preview-only` and inspect its source-linked raw evidence. Supply a JSON curation object if editing drafts:

```json
{
  "recordings": [
    {
      "recording_id": "stable-source-id",
      "summary": "Brief intended discussion topic",
      "ai_draft": "Grounded punctuation and wording",
      "uncertainties": [{"span": {"start": 1.2, "end": 2.5}, "question": "Only a meaning-changing question"}],
      "supporting_attempt_paths": [],
      "resolution_evidence": []
    }
  ]
}
```

Run again without preview and with `--curation JSON_PATH` to save a versioned view. It preserves previous human fields and copies prior review versions. The generator does not perform semantic analysis: the current Codex task does that from the evidence. It rejects curation pretending to provide human approval or silently removing an old question without resolution evidence. Known questions remain unresolved until evidence actually addresses them.

For a relevant uncertain passage, call `verify_recording.py --inbox INBOX --batch-id ID --recording-id RID --config CONFIG`. It appends a cached different-prompt check with no personal hints. It leaves the primary transcript and manifest approvals intact. This uses the same model, so do not describe it as independent-model agreement. Record the attempt reference when it changes the draft or resolves a question. When the two hypotheses disagree about meaning, keep the question. Failed/quarantined noise is shown as a recognition issue, not a fabricated idea summary.

Users may confirm a whole batch's intended meanings or only selected items; no need to request sentence-by-sentence approval. Do not group pending items as approved. Machine checking and user meaning confirmation are separate states.

## Persistent personalization

`personalize_asr.py validate --profile PROFILE` checks identity/evidence/revision consistency. `add --profile PROFILE --confirmation JSON` appends a validated human correction and preserves the old revision. The confirmation requires `id`, `recording_id`, `source_sha256`, `manifest_path`, `confirmation_ref`, `evidence_kind`, `observed`, `confirmed` and scope. `confirmation_ref` points to a saved manifest `#human_review_evidence/<id>` whose `kind` is user evidence. The executing task must verify that the evidence really came from the user; structural validation cannot authenticate an author.

Only `explicit_word_correction` contributes hints. `semantic_clarification` is not a verbatim label. Revocation is an additional evidence-linked event with `revokes`, not deletion. A profile/log revision mismatch after interruption fails for inspection; do not reset the log. Profile snapshots are private and outside this skill. A model draft must never become a confirmed correction merely through repeated machine agreement.

Compare hinted/unhinted results against sparse confirmed anchors and held-out audio; full word/character error rates require independent verbatim references. `evaluate_asr.py` stores runs outside production and can `--resume RUN_DIR` only when fingerprints match. Pin code, model hashes and dependency versions before promotion. `bootstrap_local_asr.py` builds the first candidate in an isolated runtime; it refuses an existing divergent candidate configuration. Do not install additional candidates without an observed need or overwrite the active configuration while evaluating.

## Approved automatic routing

For configured, owner-approved rule mode, use `voice_inbox.py scan/prepare/apply/status` and [rule routing](rule-routing.md). This supersedes a batch-wide review gate for explicit records: clear items are machine-interpreted archives, substantive uncertainties remain pending. ASR personalization still requires actual user word correction; automatic record intake is not a training label. Source reads/hashes are bounded to avoid one iCloud placeholder blocking every independent record.
