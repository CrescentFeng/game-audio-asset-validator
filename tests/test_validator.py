import contextlib
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import wave

from asset_validator import InputError, main, validate
from make_demo import generate, write_tone


class ValidatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "assets"
        self.root.mkdir()
        self.csv = self.base / "manifest.csv"

    def manifest(self, rows):
        with self.csv.open("w", newline="", encoding="utf-8") as out:
            writer = csv.writer(out)
            writer.writerow(["path", "sample_rate", "channels", "bit_depth"])
            writer.writerows(rows)

    def codes(self, **kwargs):
        return {i["code"] for i in validate(self.root, self.csv, **kwargs)["issues"]}

    def test_valid_and_read_only(self):
        wav = self.root / "ui_confirm.wav"
        write_tone(wav, 440)
        self.manifest([[wav.name, 48000, 1, 16]])
        before = hashlib.sha256(wav.read_bytes()).hexdigest()
        self.assertEqual(validate(self.root, self.csv)["error_count"], 0)
        self.assertEqual(hashlib.sha256(wav.read_bytes()).hexdigest(), before)

    def test_metadata_and_name(self):
        write_tone(self.root / "Bad Name.wav", 440, rate=44100, channels=2)
        self.manifest([["Bad Name.wav", 48000, 1, 16]])
        self.assertEqual(self.codes(), {"invalid_name", "metadata_mismatch"})

    def test_duplicates(self):
        for name in ("a.wav", "b.wav"):
            write_tone(self.root / name, 440)
        self.manifest([["a.wav", 48000, 1, 16], ["b.wav", 48000, 1, 16], ["A.wav", 48000, 1, 16]])
        self.assertEqual(self.codes(), {"duplicate_audio", "duplicate_manifest_path"})

    def test_missing_and_unlisted(self):
        write_tone(self.root / "a.wav", 440)
        self.manifest([["missing.wav", 48000, 1, 16]])
        self.assertEqual(self.codes(), {"missing_file", "unlisted_file"})

    def test_unsafe_paths(self):
        self.manifest([[p, 48000, 1, 16] for p in ("../outside.wav", "/tmp/a.wav", "C:\\a.wav", "a//b.wav", "./a.wav")])
        report = validate(self.root, self.csv)
        self.assertEqual(report["error_count"], 5)
        self.assertEqual(self.codes(), {"unsafe_path"})

    def test_unsafe_paths_redacted_in_text_and_json(self):
        private_paths = [str(self.base / "private.wav"), "C:\\private\\clip.wav", "../private/clip.wav"]
        self.manifest([[path, 48000, 1, 16] for path in private_paths])
        report = validate(self.root, self.csv)
        self.assertTrue(all(item["path"] == "" for item in report["issues"]))
        self.assertTrue(all("CSV row" in item["detail"] for item in report["issues"]))
        for format_name in ("text", "json"):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                code = main([str(self.root), str(self.csv), "--format", format_name])
            self.assertEqual(code, 1)
            rendered = output.getvalue()
            self.assertNotIn("private", rendered)
            self.assertNotIn(str(self.base), rendered)
            self.assertNotIn("clip.wav", rendered)

    def test_malformed_row_does_not_echo_private_path(self):
        private_path = str(self.base / "secret.wav")
        self.manifest([[private_path, 48000], [private_path, 48000, 1, 16, "extra"]])
        report = validate(self.root, self.csv)
        self.assertEqual(report["error_count"], 2)
        self.assertNotIn(private_path, json.dumps(report))
        self.assertTrue(all(item["path"] == "" for item in report["issues"]))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO creation unavailable")
    def test_fifo_wav_is_rejected_without_blocking(self):
        os.mkfifo(self.root / "stream.wav")
        self.manifest([["stream.wav", 48000, 1, 16]])
        result = subprocess.run(
            [sys.executable, "-B", str(Path(__file__).resolve().parents[1] / "asset_validator.py"),
             str(self.root), str(self.csv), "--format", "json"],
            capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertEqual(report["assets_checked"], 0)
        self.assertEqual({item["code"] for item in report["issues"]}, {"non_regular_file", "missing_file"})

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO creation unavailable")
    def test_fifo_manifest_is_rejected_without_blocking(self):
        os.mkfifo(self.csv)
        result = subprocess.run(
            [sys.executable, "-B", str(Path(__file__).resolve().parents[1] / "asset_validator.py"),
             str(self.root), str(self.csv), "--format", "json"],
            capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertIn("input_error", json.loads(result.stdout))

    def test_symlinks_not_followed(self):
        outside = self.base / "outside.wav"
        write_tone(outside, 440)
        try:
            (self.root / "link.wav").symlink_to(outside)
            (self.root / "dir").symlink_to(self.base, target_is_directory=True)
        except OSError:
            self.skipTest("Symlink creation unavailable")
        self.manifest([["link.wav", 48000, 1, 16]])
        self.assertEqual(self.codes(), {"symlink", "missing_file"})
        self.assertEqual(validate(self.root, self.csv)["assets_checked"], 0)

    def test_invalid_wav(self):
        (self.root / "bad.wav").write_bytes(b"not a WAV")
        self.manifest([["bad.wav", 48000, 1, 16]])
        self.assertEqual(self.codes(), {"unreadable_wav"})

    def test_truncated_wav(self):
        wav = self.root / "short.wav"
        write_tone(wav, 440)
        wav.write_bytes(wav.read_bytes()[:-10])
        self.manifest([[wav.name, 48000, 1, 16]])
        self.assertEqual(self.codes(), {"truncated_audio"})

    def test_empty_wav(self):
        with wave.open(str(self.root / "empty.wav"), "wb") as wav:
            wav.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        self.manifest([["empty.wav", 48000, 1, 16]])
        self.assertEqual(self.codes(), {"empty_audio"})

    def test_size_limit(self):
        write_tone(self.root / "a.wav", 440)
        self.manifest([["a.wav", 48000, 1, 16]])
        self.assertEqual(self.codes(max_bytes=10), {"file_too_large"})

    def test_bad_metadata(self):
        write_tone(self.root / "a.wav", 440)
        self.manifest([["a.wav", "not-a-number", 1, 16]])
        self.assertEqual(self.codes(), {"invalid_metadata"})

    def test_bad_header_and_root(self):
        self.csv.write_text("wrong,header\n", encoding="utf-8")
        with self.assertRaises(InputError):
            validate(self.root, self.csv)
        with self.assertRaises(InputError):
            validate(self.base / "absent", self.csv)

    def test_empty_manifest(self):
        self.manifest([])
        self.assertEqual(self.codes(), {"empty_manifest"})

    def test_malformed_row(self):
        self.manifest([["a.wav", 48000], ["b.wav", 48000, 1, 16, "extra"]])
        self.assertEqual(self.codes(), {"invalid_row"})

    def test_nested_path(self):
        (self.root / "ui").mkdir()
        write_tone(self.root / "ui" / "a.wav", 440)
        self.manifest([["ui/a.wav", 48000, 1, 16]])
        self.assertEqual(self.codes(), set())

    def test_pcm_widths(self):
        rows = []
        for width in (1, 2, 3, 4):
            name = "pcm_{}.wav".format(width * 8)
            with wave.open(str(self.root / name), "wb") as wav:
                wav.setparams((1, width, 48000, 0, "NONE", "not compressed"))
                wav.writeframes(b"\x00" * width * 16)
            rows.append([name, 48000, 1, width * 8])
        self.manifest(rows)
        self.assertEqual(self.codes(), set())
        self.assertEqual(validate(self.root, self.csv)["assets_checked"], 4)

    def test_deterministic_report(self):
        write_tone(self.root / "Bad.wav", 440)
        self.manifest([["missing.wav", 48000, 1, 16]])
        self.assertEqual(validate(self.root, self.csv), validate(self.root, self.csv))

    def test_non_wav_is_ignored(self):
        write_tone(self.root / "a.wav", 440)
        (self.root / "notes.txt").write_text("Synthetic notes", encoding="utf-8")
        self.manifest([["a.wav", 48000, 1, 16]])
        self.assertEqual(self.codes(), set())

    def test_invalid_byte_limit(self):
        self.manifest([])
        with self.assertRaises(InputError):
            validate(self.root, self.csv, max_bytes=0)

    def test_cli_exit_codes_and_json(self):
        good = self.base / "good"
        bad = self.base / "bad"
        generate(good)
        generate(bad, invalid=True)
        for location, expected in ((good, 0), (bad, 1)):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main([str(location / "assets"), str(location / "manifest.csv"), "--format", "json"])
            self.assertEqual(code, expected)
            self.assertIn("issues", json.loads(output.getvalue()))
            self.assertNotIn(str(self.base), output.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = main([str(self.root), str(self.csv), "--format", "json"])
        self.assertEqual(code, 2)
        self.assertIn("input_error", json.loads(output.getvalue()))

    def test_generator_refuses_overwrite(self):
        demo = self.base / "demo"
        generate(demo)
        with self.assertRaises(FileExistsError):
            generate(demo)


if __name__ == "__main__":
    unittest.main()
