"""Behavior tests with synthetic recordings and user evidence; no personal transcripts."""
from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

from asr_support import effective_config, file_lock, read_json, sha256, write_json
from local_asr import RecognitionError, normalize_whisper, quality_warnings, recognize
from personalize_asr import add, validate_profile
from prepare_review import prepare
import scan_and_transcribe as scanner


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.inbox = self.root / "inbox"
        self.audio = self.source / "one.m4a"
        self.audio.write_bytes(b"synthetic audio fixture")
        self.executable = self.root / "recognizer"
        self.executable.write_text(f"#!{sys.executable}\nimport json\nprint(json.dumps({{'text':'synthetic recognized sentence','segments':[]}}))\n")
        self.executable.chmod(0o755)

    def scan(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        argv = ["scan", str(self.source), str(self.inbox), "--batch-id", "today", "--transcriber", str(self.executable), *extra]
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = scanner.main()
            except SystemExit as error:
                code = error.code
        return code, json.loads(out.getvalue()) if out.getvalue() else None, err.getvalue()

    def test_adapter_success_empty_bad_json_nonzero_timeout(self):
        for completed, expected in [
            (subprocess.CompletedProcess([], 0, '{"text":"ok"}', ''), None),
            (subprocess.CompletedProcess([], 0, '{"text":""}', ''), None),
            (subprocess.CompletedProcess([], 0, 'bad JSON', ''), 'invalid_output'),
            (subprocess.CompletedProcess([], 7, '{"error":{"kind":"engine_failed"}}', ''), 'engine_failed'),
        ]:
            with patch.object(scanner.subprocess, "run", return_value=completed):
                code, data, _ = scanner.transcribe(self.audio, str(self.executable))
                self.assertEqual(code, completed.returncode)
                if expected:
                    self.assertEqual(data["error"]["kind"], expected)
        with patch.object(scanner.subprocess, "run", side_effect=subprocess.TimeoutExpired('fixture', 1)):
            code, data, _ = scanner.transcribe(self.audio, str(self.executable), timeout=1)
            self.assertEqual(data["error"]["kind"], "timeout")
            self.assertEqual(code, 1)

    def test_whisper_offsets_in_seconds(self):
        text, segments = normalize_whisper({"transcription": [{"text": " hi ", "offsets": {"from": 1250, "to": 2500}}]})
        self.assertEqual(text, "hi")
        self.assertEqual(segments[0]["start"], 1.25)

    def test_silence_not_accepted_as_a_model_idea(self):
        wav = self.root / "silence.wav"
        with wave.open(str(wav), "w") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\0\0" * 16000)
        with self.assertRaises(RecognitionError) as context:
            recognize(wav, {"engine": "whisper-cpp", "binary": "/does/not/exist", "model_path": "/does/not/exist"})
        self.assertEqual(context.exception.kind, "empty_transcript")

    def test_suspicious_output_is_kept_but_not_promoted_to_an_idea(self):
        config = self.root / "config.json"
        write_json(config, {"engine": "whisper-cpp"})
        primary = {"text": "thanks for watching", "segments": [], "warnings": [{"kind": "possible_boilerplate"}]}
        with patch.object(scanner, "duration", return_value=10), patch.object(scanner, "transcribe", return_value=(0, primary, "")), patch.object(scanner, "recognize", return_value={"text": "unrelated", "segments": []}):
            code, summary, _ = self.scan("--transcriber-config", str(config))
        self.assertEqual(code, 0)
        self.assertEqual(summary["failed"], 1)
        batch = read_json(self.inbox / "batches/today/batch.json")
        self.assertEqual(batch["sources"][0]["status"], "failed")
        self.assertEqual(batch["raw_transcriptions"][0]["response"]["text"], "thanks for watching")
        review, _ = prepare(batch, {})
        self.assertEqual(review["recordings"], [])
        self.assertEqual(len(list((self.inbox / "batches/today/raw").glob('*.json'))), 2)

    def test_retry_limit_is_per_config_and_verification_does_not_consume_it(self):
        record = {"recognition_identity": "one", "stage": "primary", "response": {"error": {"kind": "empty_transcript", "retryable": False}}, "finished_at_utc": "2020-01-01T00:00:00+00:00"}
        prior = [(Path('1'), record), (Path('2'), record)]
        self.assertFalse(scanner.retry_allowed(prior, "one", False))
        self.assertTrue(scanner.retry_allowed(prior, "two", False))
        check = {**record, "stage": "verification"}
        self.assertTrue(scanner.retry_allowed([(Path('1'), record), (Path('2'), check)], "one", False))

    def test_no_probability_is_called_accuracy(self):
        warnings = quality_warnings("hello sentence", [{"tokens": [{"text": "hello", "p": 0.01}]}])
        self.assertEqual(warnings[0]["kind"], "weak_decoder_evidence")

    def test_rename_and_repeat_do_not_duplicate(self):
        self.assertEqual(self.scan()[1]["transcribed"], 1)
        self.audio.rename(self.source / "renamed.m4a")
        code, summary, _ = self.scan()
        self.assertEqual(code, 0)
        self.assertEqual(summary["already_processed"], 1)
        self.assertEqual(len(list((self.inbox / "batches/today/raw").glob('*.json'))), 1)

    def test_historical_retry_stays_in_original_and_preserves_confirmation(self):
        old = self.inbox / "batches/old/batch.json"
        batch = {"schema_version": 1, "batch_id": "old", "status": "needs_human_review", "sources": [{"id": "kept-id", "sha256": sha256(self.audio), "status": "failed"}], "raw_transcriptions": [], "items": [{"human_confirmed_text": "kept"}], "groups": [{"id": "kept-group"}], "review": {"version": 2, "recordings": [{"recording_id": "kept-id", "human_confirmed_text": "kept", "confirmation_evidence": "user-message"}]}}
        write_json(old, batch)
        code, summary, _ = self.scan()
        self.assertEqual(code, 0)
        self.assertEqual(summary["retried"], 1)
        result = read_json(old)
        for key in ["status", "items", "groups", "review"]:
            self.assertEqual(result[key], batch[key])
        self.assertEqual(result["sources"][0]["id"], "kept-id")
        self.assertFalse((self.inbox / "batches/today/batch.json").exists())

    def test_same_day_empty_retry_is_deferred_and_state_preserved(self):
        with patch.object(scanner, "transcribe", return_value=(0, {"text": ""}, "")):
            self.scan()
            manifest = self.inbox / "batches/today/batch.json"
            data = read_json(manifest)
            data["status"] = "needs_human_review"
            write_json(manifest, data)
            code, summary, _ = self.scan()
        self.assertEqual(summary["retry_deferred"], 1)
        self.assertEqual(read_json(manifest)["status"], "needs_human_review")

    def test_numbering_gap_cannot_overwrite_attempt(self):
        directory = self.root / "raw"
        directory.mkdir()
        write_json(directory / "rec.attempt-03.json", {"text": "keep"})
        before = sha256(directory / "rec.attempt-03.json")
        new = scanner.save_attempt(directory, "rec", {"text": "new"})
        self.assertEqual(new.name, "rec.attempt-04.json")
        self.assertEqual(sha256(directory / "rec.attempt-03.json"), before)

    def test_corrupt_manifest_fails_without_duplicate_batch(self):
        bad = self.inbox / "batches/old/batch.json"
        bad.parent.mkdir(parents=True)
        bad.write_text('{bad')
        code, summary, err = self.scan()
        self.assertEqual(code, 2)
        self.assertIn(str(bad), err)
        self.assertFalse((self.inbox / "batches/today/batch.json").exists())

    def test_other_process_cannot_write_during_lock(self):
        script = Path(scanner.__file__)
        with file_lock(self.inbox / ".intake.lock"):
            completed = subprocess.run([sys.executable, str(script), str(self.source), str(self.inbox), "--transcriber", str(self.executable)], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 2)
        self.assertIn("another process", completed.stderr)
        self.assertFalse((self.inbox / "batches").exists())

    def test_interrupted_manifest_write_resumes_existing_raw(self):
        with patch.object(scanner, "persist_batch", side_effect=RuntimeError("interrupted")):
            code, _, _ = self.scan()
        self.assertEqual(code, 2)
        raw = next((self.inbox / "batches/today/raw").glob('*.json'))
        before = sha256(raw)
        with patch.object(scanner, "transcribe", side_effect=AssertionError("must reuse completed raw")):
            code, summary, _ = self.scan()
        self.assertEqual(code, 0)
        self.assertEqual(summary["recovered"], 1)
        self.assertEqual(sha256(raw), before)

    def test_review_partial_confirmation_and_machine_boundary(self):
        batch = {"batch_id": "fixture", "sources": [{"id": "one", "source_path": "/fixture/one.m4a", "sha256": "abc"}], "raw_transcriptions": [{"id": "one", "response": {"text": "raw"}}], "review": {"version": 1, "recordings": [{"recording_id": "one", "review_id": "R01", "confirmation_evidence": "real-user", "human_confirmed_text": "kept", "ai_draft": "old", "uncertainties": []}]}}
        result, markdown = prepare(batch, {"recordings": [{"recording_id": "one", "ai_draft": "new"}]})
        self.assertEqual(result["recordings"][0]["human_confirmed_text"], "kept")
        self.assertEqual(batch["review"]["recordings"][0]["ai_draft"], "old")
        with self.assertRaises(ValueError):
            prepare(batch, {"recordings": [{"recording_id": "one", "human_confirmed_text": "forged"}]})

    def test_review_cannot_silently_remove_existing_question(self):
        batch = {"batch_id": "fixture", "sources": [{"id": "one", "source_path": "/fixture/a.m4a"}], "raw_transcriptions": [{"id": "one", "response": {"text": "raw"}}], "review": {"recordings": [{"recording_id": "one", "review_id": "R01", "ai_draft": "draft", "uncertainties": ["unknown word"]}]}}
        with self.assertRaises(ValueError):
            prepare(batch, {"recordings": [{"recording_id": "one", "uncertainties": []}]})

    def make_confirmation(self, **updates):
        manifest = self.root / "batch.json"
        write_json(manifest, {"sources": [{"id": "fixture", "sha256": "fixture-hash"}], "human_review_evidence": [{"id": "C1", "kind": "user_clarification", "text": "Confirmed term ALPHA; revoke previous correction."}]})
        confirmation = {"id": "correct-1", "recording_id": "fixture", "source_sha256": "fixture-hash", "manifest_path": str(manifest), "confirmation_ref": str(manifest) + "#human_review_evidence/C1", "evidence_kind": "explicit_word_correction", "observed": "wrong", "confirmed": "ALPHA", "scope": "personal"}
        confirmation.update(updates)
        path = self.root / (confirmation["id"] + ".json")
        write_json(path, confirmation)
        return path

    def test_profile_persists_rejects_machine_gold_and_can_revoke(self):
        profile = self.root / "profile/profile.json"
        confirmation = self.make_confirmation()
        self.assertEqual(add(profile, confirmation)["active"], 1)
        self.assertEqual(add(profile, confirmation)["status"], "already_imported")
        config = {"personalization_enabled": True, "profile_path": str(profile)}
        self.assertEqual(effective_config(config)["prompt_terms"], ["ALPHA"])
        process = subprocess.run([sys.executable, str(Path(__file__).with_name("personalize_asr.py")), "validate", "--profile", str(profile)], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0)
        machine = self.make_confirmation(id="machine", evidence_kind="machine_checked")
        with self.assertRaises(ValueError):
            add(profile, machine)
        revoked = self.make_confirmation(id="revoke", evidence_kind="revocation", revokes="correct-1")
        self.assertEqual(add(profile, revoked)["active"], 0)
        self.assertEqual(effective_config(config)["prompt_terms"], [])
        self.assertEqual(validate_profile(profile)["revision"], 2)


if __name__ == "__main__":
    unittest.main()
