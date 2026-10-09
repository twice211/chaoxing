"""Failed configuration writes never destroy the previous usable file."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import Config
from utils import settings_io as settings


class AtomicSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.target = self.root / "user_config.py"
        self.original = b"# personal notes\nCUSTOM_VALUE = 17\nAI_MODEL = 'original'\n"
        self.target.write_bytes(self.original)
        self.values = settings.load_current(Config())
        self.values.update(AI_MODEL="updated", AI_API_KEY="fixture-private-key")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def assert_original_and_no_temporary_credentials(self) -> None:
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual([path for path in self.root.rglob("*") if path.is_file()], [self.target])

    def test_syntax_validation_happens_before_destination_changes(self) -> None:
        with patch("utils.settings_io.ast.parse", side_effect=SyntaxError("fixture invalid syntax")):
            with self.assertRaises(ValueError):
                settings.save(self.values, self.target)
        self.assert_original_and_no_temporary_credentials()

    def test_nonfinite_number_is_rejected_before_write(self) -> None:
        self.values["AI_TEMPERATURE"] = float("nan")
        with self.assertRaises(ValueError):
            settings.save(self.values, self.target)
        self.assert_original_and_no_temporary_credentials()

    def test_replace_failure_retains_original_and_cleans_private_temp_file(self) -> None:
        observed = []
        def fail_replace(source, destination):
            path = Path(source)
            observed.append(path)
            self.assertTrue(path.resolve().is_relative_to((self.root / "data").resolve()))
            self.assertIn("fixture-private-key", path.read_text(encoding="utf-8"))
            self.assertEqual(Path(destination), self.target)
            raise OSError("fixture replacement denied")
        with patch("os.replace", side_effect=fail_replace):
            with self.assertRaises(OSError):
                settings.save(self.values, self.target)
        self.assertEqual(len(observed), 1)
        self.assert_original_and_no_temporary_credentials()

    def test_flush_failure_does_not_replace_original(self) -> None:
        with patch("os.fsync", side_effect=OSError("fixture disk failure")):
            with self.assertRaises(OSError):
                settings.save(self.values, self.target)
        self.assert_original_and_no_temporary_credentials()

    def test_success_preserves_custom_content_and_replaces_one_managed_block(self) -> None:
        settings.save(self.values, self.target)
        self.values["AI_MODEL"] = "new-model"
        settings.save(self.values, self.target)
        text = self.target.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(self.original.decode()))
        self.assertEqual(text.count(settings.MARK_START), 1)
        self.assertEqual(text.count(settings.MARK_END), 1)
        cfg = Config()
        settings.reload_into(cfg, self.target)
        self.assertEqual(cfg.get("AI_MODEL"), "new-model")
        self.assertEqual(cfg.get("CUSTOM_VALUE"), 17)
        self.assertEqual(cfg.get("AI_API_KEY"), "fixture-private-key")

    def test_same_timestamp_and_file_size_reload_reads_new_source(self) -> None:
        self.target.write_text('AI_MODEL = "first"\n', encoding="utf-8")
        stamp = self.target.stat()
        cfg = Config()
        settings.reload_into(cfg, self.target)
        self.target.write_text('AI_MODEL = "other"\n', encoding="utf-8")
        os.utime(self.target, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        settings.reload_into(cfg, self.target)
        self.assertEqual(cfg.get("AI_MODEL"), "other")

    def test_unbalanced_managed_block_is_preserved_and_rejected(self) -> None:
        self.original += (settings.MARK_START + "\nCUSTOM_TAIL = 'must retain'\n").encode()
        self.target.write_bytes(self.original)
        with self.assertRaises(ValueError):
            settings.save(self.values, self.target)
        self.assert_original_and_no_temporary_credentials()


if __name__ == "__main__":
    unittest.main()
