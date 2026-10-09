---
name: voice-idea-curation
description: "Curate voice recordings into source-linked items, route clear content through owner-approved project intake rules, aggregate exceptions and prepare scoped proposals. Use for voice intake, record routing and grouped handoff; never adopt guidance or implement project improvements without specific approval."
---

# Voice Idea Curation

Keep immutable audio and raw recognition, machine interpretation, and actual user confirmation distinct. Recording accumulation does not train the acoustic model. Personal experiences are reports, not general scientific facts.

## Choose the authorized mode

For an inbox with `routing/config.json` and owner-approved `routing/rules.json`, use the rule-driven CLI in [rule routing](references/rule-routing.md). Clear content is automatically archived under those rules, marked machine-interpreted. Ask only questions that materially affect meaning, destination, dates, values or writes. Split mixed recordings: an uncertain cause must not hold back an unambiguous measurement.

Without an approved rule configuration, use the original workflow: raw transcription → machine draft → human review → source-linked independent items → project/topic handoff. Do not infer authority to write project records from a candidate project match.

## Intake and source evidence

Read [local recognition](references/local-recognition.md) for the configured local adapter, retry limits and personal correction evidence. Run `voice_inbox.py --inbox INBOX scan` in configured mode. Preserve audio and every raw recognition attempt. Never download or promote new models during a daily run. Immutable attempts, batch IDs, hashes, original quotes and current audio locations remain traceable. Failures retain retry eligibility; do not reset or force retry limits.

For standalone intake, `create_batch.py` and `scan_and_transcribe.py` remain available. Read [batch contract](references/batch-record.md) before editing batch fields. `prepare_review.py` can save versioned comparisons, preserving human fields. Machine checking does not create human approval. For a suspicious passage only, `verify_recording.py` appends a different-prompt attempt from the same model; it is not independent confirmation.

## Rule-driven curation

1. CLI `prepare` supplies immutable successful source records and the exact rules hash. Inspect raw wording and existing user evidence; do not infer content from place-based filenames. Previously handed-off batches are not reopened automatically.
2. Codex splits source-linked items and writes `voice-routing-proposal/v1` JSON in the private inbox. The CLI does not contain a semantic model. Use stable item keys and separate literal `quote`, machine `ai_draft`, nature and questions.
3. CLI `apply --proposal FILE` checks rules, sources, project capabilities and receipts. Rejections remain eligible. A source is marked processed only when all submitted items have a verified receipt or retained queue record. Include every meaningful span, including unresolved/private content; a CLI cannot detect omitted meaning.
4. CLI `status` aggregates by project and issue; `show` queries the project action/issue archive. On meaningful change, provide a short phone-readable summary and essential grouped questions. Repeat scans and known unchanged failures stay quiet.

Use the configured life-projects `demand` route for a source-supported need that belongs in the demand lifecycle. Keep ordinary fitness observations, measurements, meals, P02 revision cards and P03 meal logs on their approved routes. A life intake receipt means the source was archived and linked to a demand; it does not mean the requested work or target project upgrade is complete. After daily routing, use the configured life project entrypoint to run `python -m app.demands sync`, `check` and `--json show` from that project root for plan observation and a concise demand summary. Decisions, project changes and execution handoff still require the applicable human authorization and tool checks. Read [rule routing](references/rule-routing.md) for the demand payload and exact queued replay.

For a new known-exercise observation or issue with a narrow `issue_key`, inspect its discussion card after `apply` and submit a semantic `voice-revision-draft/v1` with `revise --input FILE`. Include the exact current evidence event IDs, current voice-guidance ref and prior card ref; choose `propose_change` only for a concrete personal wording and applicability, otherwise `no_change` with a reason and remaining questions. This updates the card projection without re-archiving audio. New evidence makes an older card draft stale until reviewed. The card is a machine discussion draft, not an adopted technique or training plan. Group unknown exercise separately without inventing a UID.

Read [project interface](references/project-interface.md) for the target-owned capability contract. Exact project names/aliases and internal state live in that project. Do not copy them into this skill. Unknown project, action, date or session remains unknown. “Today” needs verified original audio creation metadata; scan/batch/filesystem timestamps are not recording dates.

## Proposals and decisions

When records reveal a related gap, inspect only relevant project capabilities. A proposal states existing capability, source/code evidence, gap, affected item IDs, smallest change and acceptance. For guidance proposals add personal applicability conditions, supporting/opposing evidence and the current base guidance reference. Present individual sensations and tentative cues as such. Related questions accumulate under the same issue key; distinct questions/actions remain separate.

Archive proposals as records only. A project improvement or new pipeline requires the user's approval of that specific version before implementation/enabling. Adopt guidance only through `decide` with the exact proposal ID/hash and real saved user-message evidence. The running task authenticates that evidence; local JSON cannot prove authorship. Automatic archiving never counts as confirmation or scientific validation. Do not change adopted training plans, old Excel, source audio, shortcuts, other project code or global governance during a daily intake run.

When a queued item has a newly approved route, use `prepare-queued --event-id ID` to recover its frozen source and draft; replay only that item with `replaces_queued_event_id`. This does not rerun recognition or reopen completed batches. An unresolved destination or intent remains `needs_destination_or_intent` until concrete evidence supports routing; do not present it as a specific approval request.

User corrections append a new source-linked item with `supersedes_event_id` and actual correction evidence; never overwrite prior records. Personal terminology accepts only genuine user word corrections through `personalize_asr.py`; semantic rewrites are not acoustic labels.

## Completion

Finish with each supplied source retained as received, queued or failed, plus a concise changed-state summary. No batch-wide human gate in approved rule mode. Keep substantive ambiguities pending without blocking independent items. The existing daily heartbeat is the only scheduler; actual 17:00 triggering and lubao connectivity require real evidence, and manual CLI success proves neither. Direct dictation into another application is a separate lane and must not silently enter the inbox.
