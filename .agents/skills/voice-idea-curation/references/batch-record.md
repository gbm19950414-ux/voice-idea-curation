# Batch record contract

Each run has one user-selected batch directory. Its `batch.json` is the canonical structured record and its Markdown files are readable views, not competing sources of truth.

The directory contains:

```text
batch.json
raw/
review/
handoff/
```

`batch.json` contains `schema_version`, `batch_id`, `created_at`, `skill`, `status`, `sources`, `items`, and `groups`. The skill field records at least the skill name and the version or commit when known.

Each source has an immutable `recording_id`, original path, filename, content hash when available, duration when available, and a processing status. A raw recognition record identifies its engine, version, configuration, timestamps when supplied by that engine, and unedited text.

Each item has a stable `item_id`, one or more source references, an AI draft, a human-confirmed field or an explicit pending state, candidate project IDs, its chosen project or `unassigned`, item nature, related item IDs, and open questions. `groups` refer to item IDs and state the common discussion object and a proposed project-opening prompt.

Never overwrite raw recognition with curation or confirmed wording. Preserve a previous AI proposal when replacing it. Mark a source as `failed` with an error summary when recognition cannot complete.

The default state progression is `new`, `recognized`, `needs_human_review`, `reviewed`, `grouped`, and `handed_off`. A batch can contain mixed states while it is in progress.

Schema v1 accepts optional local-recognition fields. Each immutable attempt can have `recognition_identity`, `stage` (`primary` or `verification`), `configuration`, model/config/profile hashes and unedited engine response. `raw_transcriptions` is the latest primary result cache; verification attempts do not replace it. Suspicious output may remain in a failed attempt with `uncertain_transcript`, but must not be promoted to an idea merely because it is nonempty.

`review` contains versioned `recordings` with stable source IDs, `ai_draft`, `summary`, `uncertainties`, `machine_review`, and distinct `human_confirmed_text` / `confirmation_evidence`. `machine_checked` describes text review, not human confirmation. A confirmation of intended meaning is not a verbatim acoustic label. Previous views are preserved in `review/versions/`; the current Markdown is a derived view. New sources may require new review without revoking existing approvals. Failed retries must not lower a batch's existing reviewed state.
