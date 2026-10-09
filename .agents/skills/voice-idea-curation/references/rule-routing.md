# Rule-driven CLI (v1)

One CLI serves manual runs, the existing 17:00 Codex heartbeat and optional lubao queries. No operation window or second scheduler is required. Product scripts own validation/storage; Codex owns semantic curation. Audio and personal state stay outside the skill repository.

```sh
python3 scripts/voice_inbox.py --inbox /absolute/private/inbox --json scan
python3 scripts/voice_inbox.py --inbox /absolute/private/inbox --json prepare
python3 scripts/voice_inbox.py --inbox /absolute/private/inbox --json prepare-queued --event-id VOICE_EVENT_ID
python3 scripts/voice_inbox.py --inbox /absolute/private/inbox --json apply --proposal /absolute/private/proposal.json
python3 scripts/voice_inbox.py --inbox /absolute/private/inbox --json status
python3 scripts/voice_inbox.py --inbox /absolute/private/inbox show --exercise 深蹲
python3 scripts/voice_inbox.py --inbox /absolute/private/inbox show --issue squat-depth
python3 scripts/voice_inbox.py --inbox /absolute/private/inbox --json revise --input /absolute/private/revision-draft.json
python3 scripts/voice_inbox.py --inbox /absolute/private/inbox --json decide --input /absolute/private/decision.json
```

`routing/config.json` schema `voice-routing-config/v1` owns source directory, subject ID, active recognizer config, project paths/interpreters/private data roots. `routing/rules.json` schema `voice-routing/v1` declares a monotonic version, owner authorization, and each `route_id/project_id/operation/natures`; annotate acceptance and clarification conditions. Rules are not executable arbitrary commands. Project interface validates the consumer and capability. Paths are resolved from project-owned metadata; receiver verifies Gitignored product-local personal stores.

A proposal matches the exact canonical rules SHA256 from `prepare`:

```json
{
  "schema_version": "voice-routing-proposal/v1",
  "rules_sha256": "64-character canonical hash from prepare",
  "items": [{
    "source_sha256": "audio hash from prepare",
    "route_id": "fitness-observation",
    "item_key": "observation-1",
    "quote": "exact substring of raw recognition",
    "ai_draft": "machine interpretation, retaining uncertainty",
    "nature": "observation",
    "exercise": "exact known name, alias, or unresolved spoken name",
    "issue_key": "stable, narrowly scoped topic",
    "questions": []
  }]
}
```

Measurement adds `measurement:{metric,value,unit,subject_id?,date?,date_evidence?}` with decimal string values; current metrics `bodyweight/kg` and `abdominal_skinfold/mm`. Configured owner is the default only for their own report; named third-party reports are held. Unit/metric/date ambiguity preserves a candidate. `date_evidence` is a literal full spoken date or real owner clarification. Otherwise “today” resolves only from verified original audio container creation metadata. No artificial midnight timestamps; day precision uses `daily-measurement/v1` in the existing private store. Current readers must explicitly opt into this type.

Project/guidance proposal adds `proposal:{existing_capability,gap,minimal_change,acceptance,evidence,affected_items?}`; guidance additionally needs `conditions,base_guidance_ref,supporting/opposing evidence`. Attach source-linked evidence and relevant code paths/hashes in proposal evidence. A source quotation alone does not establish an architectural gap. `session_ref` is accepted only if an actual published session artifact exists; otherwise preserve the observation without a session link.

Approved P02-v2: For a known exercise and narrow `issue_key`, a new observation or question can include `revision:{schema_version:voice-revision-draft/v1,status:propose_change|no_change,base_guidance_ref,proposed_wording?,conditions?,supporting_event_ids:[],opposing_event_ids:[],review_questions:[]}`. Read `show --exercise NAME` first to get the current voice-lane guidance ref and related event IDs. State a proposed personal wording only when the source meaning and applicability support it; otherwise use `no_change` with open questions. The project validates cited events belong to the same exercise/issue and returns versioned `revision_cards`; no card adopts a guideline. Existing cycle/live guidance is outside this voice-lane projection, so mark it for separate review when material.

For already archived and newly routed observations, prefer the independent `revise` command so source captures remain singular. Its JSON also requires `source_event_ids` in current card order, `base_revision_ref` (the current `semantic_revision_ref` or null), and for `no_change` a nonempty `reason` with null `proposed_wording`. Query the current card immediately before writing. The command appends an immutable machine-authored revision draft; a repeated identical request is a duplicate, while a new source or adopted voice guidance requires a new base. A card with no defensible wording explicitly says `no_change`, never implies approval. During daily processing, review each changed known-action/issue card and submit a specific draft or `no_change`; do not leave every card at `awaiting_semantic_draft`.

Approved P03-v2: Route clear meal reports to `fitness-meal-log` with `nature:diet_record` and `meal_log:{schema_version:voice-meal-log/v1,date:null|YYYY-MM-DD,entries:[{meal,food_category,dish_name:null|string,food_anchor?,amount:null|{value,unit,basis,qualifier,quote}}]}`. Use `g` for spoken weights, `count` for item counts; `basis:cooked_dish` means entire prepared dish cooked weight. Each quantity quote must contain its food anchor and spoken number, preventing quantities being reassigned between foods. Unknown exact dish and unknown quantities stay null. The project verifies an explicit spoken cooked-weight term, a source-bound owner clarification or the exact approved P03 convention recorded in the active rule; explicit raw weight cannot use a cooked default. User examples of Chinese dishes are not the eaten dish. Do not calculate calories or protein. A correction appends a new item with `supersedes_event_id` and genuine owner evidence; a corrected number or dish name absent from the audio must be specifically confirmed in that evidence. This route is enabled only after the exact P03-v2 approval, receiver verification and rules version increment.

No matching pipeline: use the queue route, nature and `pipeline_hint`. `status` groups queued items for a specific new-pipeline proposal: candidate project, supported operation, example source IDs, minimum change and acceptance. If destination or intent is still unclear, keep `needs_destination_or_intent` and do not invent a candidate project or call it an approval request. Save a concrete proposal in private `routing/proposals/`; only the owner's specific approval can enable it. After approved consumer implementation and validation, increment rules version and save its preimage. Do not treat queue creation as consumer success.

An earlier unspecific pipeline placeholder can become `handed_to_demand` (or `partially_handed_to_demand`) in `status` when its affected queue items have verified demand receipts. The original proposal file stays unchanged and the derived status includes demand references. This means the general demand inbox received the source, not that the downstream specialty pipeline is enabled or approved. Missing or changed receipts invalidate that handoff projection and report the verification error.

For a specific queued event, `prepare-queued --event-id ID` returns its frozen draft and source, current rules and canonical `rules_sha256`. It checks the immutable raw attempt's file SHA256 and text. Create a new `voice-routing-proposal/v1` against that rules hash with the approved destination route, the original `source_sha256`, exact `quote` and `item_key`, and `replaces_queued_event_id: ID`. `apply` resolves only that queued source from the ledger even if the original batch is no longer in `prepare`; it rejects a changed audio/raw hash, quote or item key. A successful routed event supersedes the queue entry. Repeating the same event after interrupted publication recovers the receiver's duplicate receipt; a different replacement for an already superseded queue entry is rejected. Other completed sources are not reopened. Recognition is not rerun.

For a configured life-projects `demand` capability, the semantic draft may include `demand:{title,outcome,kind,target_project,constraints}`. `kind` is `action`, `learning`, `project_upgrade` or `question`; `target_project` is `{id,path}` when evidenced, otherwise `null`. Keep `title` and `outcome` grounded in the literal quote, and use `constraints` for relevant limits without inventing approval. The producer forwards `demand` unchanged in the delivered item; the life receiver owns demand IDs and its private ledger. A verified intake receipt establishes source delivery, while the demand's decision, execution and acceptance remain separate. Existing fitness routes and P02/P03 contracts above retain their own consumers. A project upgrade requires approval of its exact scope before implementation; dot is not a requirement of this intake workflow.

Receipts and final publication manifests are verified before the upstream ledger is acknowledged; a missing receipt rejects delivery. For the life receiver's V2 format, paths must resolve inside its configured `data_root`, raw-file SHA256 must match, the artifact must retain the event ID, demand ID and exact item (apart from source `audio_path` relocation), and the manifest must bind that artifact. Legacy consumer receipts keep their original contract. Item identity uses audio/raw hashes, quote, item key and operation, independent of audio filename. Retry preserves adopted versions and raw data. Changed interpretation requires a new key plus explicit supersession evidence; same identity with changed content is rejected. Location maps may update after verifying renamed audio without rewriting provenance. Interrupted publication is invisible until its final manifest; repeat receipt recovery is safe. Inbox and target locks serialize concurrent mutation.

`decide` forwards an exact guidance decision `{proposal_id,proposal_sha256,action,evidence}`. Evidence points to SHA-verified JSON `{kind:user_message,text,message_ref,known_at,proposal_id,proposal_sha256,action}` authenticated by the running Codex task against a real user message. A new pipeline decision is saved with the concrete proposal and actual user evidence, then implemented/enabled within its separately approved scope, not as a guidance decision.

Automation runs scan → prepare → Codex semantic JSON → apply → status. Retain the single original scheduler. When a configured life route is present, use that project's configured interpreter and root for `python -m app.demands sync`, `check` and `--json show` after routing, and summarize newly linked demands, pending decisions, execution observations and unverified acceptance. `sync` observes registered plans; it does not approve, dispatch or close demands. Check the target's own documentation for command details. Emit only meaningful changes/new failures/essential unresolved questions. Source hash coverage is acknowledged only after all supplied items succeed or are queued; rejected sources remain in `prepare`. The scheduler cannot guarantee exact execution time if the Mac/app/disk is unavailable. Store actual heartbeat acceptance separately from manual tests.

Lubao uses the same absolute CLI entrypoint through its independently configured Mac connection. First verify read-only `status/show`, then a repeat `apply` with no duplicate writes. Connection not witnessed is `unknown`; do not infer connectivity from this Codex task's access.

For an exact new-pipeline decision use `decide --input FILE` with `{scope:new_pipeline,action:approve|reject,proposal_path,proposal_sha256,evidence}`. The private proposal is `voice-pipeline-proposal/v1`; `proposal_sha256` hashes its exact file bytes. Real user evidence additionally binds `scope/action/proposal_id/proposal_sha256`. This records approved-for-implementation or rejected, **never enables a rule**. After the approved consumer passes acceptance, the operating task may version the rules and replay queued drafts with `replaces_queued_event_id`; the old queue entry remains superseded and source hashes must match.

Measurement clarification evidence binds the old `event_id` (for a body candidate) or exact `source_audio_sha256` (for an unresolved queue item), plus `measurement_confirmation:{metric,value,unit,subject_id,date?}`. Only the corresponding fields may be corrected; append a new item and the real evidence. Pending candidates can resolve into date-only facts without changing raw recognition. Previously persisted partial batch review can be referenced by frozen snapshot/hash, review ID and matching recording/audio IDs; it retains its narrow scope and day precision, and cannot masquerade as full human confirmation or guidance adoption. Recording-time projections describe when the audio was created, not an invented execution/session date.
