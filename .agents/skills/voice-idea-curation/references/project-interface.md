# Project interface

A target project can opt into routing by maintaining this small file at its project root:

```yaml
schema_version: 1
project_id: body_building
display_name: BodyBuilding
summary: Personal training feedback and planning system.
terms_file: docs/terminology.md # optional, relative to this project
```

`project_id` is a stable lowercase identifier. `display_name` and `summary` help route an idea. `terms_file`, when present, names the project's own maintained terminology source. The target project owns this file and any terms it references.

When the file is absent, inspect only the minimum project material needed for candidate routing and leave the assignment as `unassigned` if its identity or fit remains unclear. Do not create or edit this interface as part of a voice-curation run unless the user explicitly asks to onboard that project.

Each delivery records the target path and repository commit when available. A target without Git records `target_commit: null` and a content fingerprint of its declared version files. The shared skill stores references only; it never mirrors target project metadata or internal demand state.

## Version 2: opt-in receiving capability

```json
{
  "schema_version": 2,
  "project_id": "example_project",
  "display_name": "Example",
  "summary": "Project-owned description.",
  "capabilities": {"demand": "voice-delivery/v1"},
  "consumer": {
    "entrypoint": "scripts/voice_intake.py",
    "receipt_format": "voice-delivery-receipt/v2",
    "artifact_schema": "voice-delivery-artifact/v2",
    "version_files": ["scripts/voice_intake.py", "app/intake.py"]
  }
}
```

`project-interface.yaml` may contain JSON (parsed first with the producer's standard library) or existing YAML (parsed by the configured target interpreter). V1 remains usable for candidate classification; V2 is required before automatic delivery. The interface's `consumer.entrypoint` and every `consumer.version_files` path must resolve to a file inside the target project. The declared `version_files` supply raw-file SHA256 values and a combined content fingerprint; if omitted, the producer hashes the entrypoint and interface. The target commit is recorded only when the project itself is a Git repository. The configured project interpreter launches the consumer, while private inbox `routing/config.json` supplies the project path and absolute `data_root`; the interface does not choose a runtime store. Supported operations must match the approved rule.

For `consumer.receipt_format: voice-delivery-receipt/v2`, a successful `receive` response has `status: accepted|duplicate`, `archive_status: archived`, `event_id`, optional `demand_id`, and this receipt:

```json
{
  "schema_version": "voice-delivery-receipt/v2",
  "event_id": "VOICE_EVENT_ID",
  "artifact": {"path": "intake/VOICE_EVENT_ID.json", "sha256": "RAW_FILE_SHA256"},
  "manifest": {"path": "receipts/VOICE_EVENT_ID.json", "sha256": "RAW_FILE_SHA256"}
}
```

Both paths are relative to configured `data_root` and must remain inside it after resolution. SHA256 values hash exact file bytes. The artifact is `{schema_version,event_id,item}` with `schema_version` set to `consumer.artifact_schema` (default `voice-delivery-artifact/v2`). The manifest is `{schema_version:"voice-delivery-manifest/v2",operation:"voice_intake",event_id,artifact:{path,sha256}}`. When the response includes `demand_id`, both files must contain the same ID; a generic receiver need not create one. The life receiver declares `artifact_schema: life-voice-intake/v1` and includes `demand_id`. The producer verifies the response and both files, including the delivered item except a changed source `audio_path`, before acknowledging the queue. A missing, changed or escaping receipt fails closed. Consumers without V2 keep their existing receipt shape and verification.

Delivery retains source/audio hashes and literal quotes. A V2 intake receipt means the source reached its project; it is not an approval, work completion or acceptance of the requested outcome. The target project owns internal storage, demand lifecycle, execution observations and any domain authority. A capability gap is a proposal, not authority to broaden a receiver or enable a new route.
