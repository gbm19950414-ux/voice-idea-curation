"""Regression tests for source discovery behavior."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scan_and_transcribe import main


class ScanAndTranscribeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.inbox = self.root / "inbox"

    def run_scan(self) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["scan_and_transcribe.py", str(self.source), str(self.inbox), "--batch-id", "test"]):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                try:
                    result = main()
                except SystemExit as error:
                    result = int(error.code)
        return result, stdout.getvalue(), stderr.getvalue()

    def test_unlistable_source_fails_before_writing_inbox(self) -> None:
        real_iterdir = Path.iterdir
        resolved_source = self.source.resolve()

        def deny_source_listing(path: Path):
            if path == resolved_source:
                raise PermissionError(1, "Operation not permitted", str(path))
            return real_iterdir(path)

        with patch.object(Path, "iterdir", deny_source_listing):
            result, stdout, stderr = self.run_scan()

        self.assertEqual(result, 2)
        self.assertEqual(stdout, "")
        self.assertIn(str(self.source), stderr)
        self.assertIn("Operation not permitted", stderr)
        self.assertFalse(self.inbox.exists())

    def test_empty_source_is_a_successful_empty_scan(self) -> None:
        result, stdout, stderr = self.run_scan()

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(json.loads(stdout), {
            "batch_id": "test",
            "discovered": 0,
            "already_processed": 0,
            "transcribed": 0,
            "failed": 0,
            "retried": 0,
        })
        self.assertFalse((self.inbox / "batches" / "test" / "batch.json").exists())

    def test_same_content_is_discovered_only_once(self) -> None:
        (self.source / "a.m4a").write_bytes(b"same audio bytes")
        (self.source / "b.m4a").write_bytes(b"same audio bytes")

        result, stdout, stderr = self.run_scan()

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        summary = json.loads(stdout)
        self.assertEqual(summary["discovered"], 2)
        self.assertEqual(summary["already_processed"], 1)
        manifest = self.inbox / "batches" / "test" / "batch.json"
        batch = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(len(batch["sources"]), 1)
        self.assertEqual(batch["sources"][0]["source_name"], "a.m4a")


if __name__ == "__main__":
    unittest.main()
