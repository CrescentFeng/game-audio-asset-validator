"""Offline, read-only validation of a small PCM WAV collection and CSV manifest."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import wave

FIELDS = ["path", "sample_rate", "channels", "bit_depth"]
NAME = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*\.wav\Z")
DEFAULT_MAX_BYTES = 128 * 1024 * 1024


class InputError(ValueError):
    """Invalid invocation or unusable manifest, rather than an asset finding."""


def validate(root, manifest, max_bytes=DEFAULT_MAX_BYTES):
    root, manifest = Path(root), Path(manifest)
    if root.is_symlink() or not root.is_dir():
        raise InputError("Asset root must be an existing, non-symlink directory")
    if max_bytes <= 0:
        raise InputError("max_bytes must be positive")
    root = root.resolve()
    try:
        if manifest.is_symlink() or not manifest.is_file():
            raise InputError("Manifest must be an existing, non-symlink regular file")
        with manifest.open(encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            if reader.fieldnames != FIELDS:
                raise InputError("CSV header must be: " + ",".join(FIELDS))
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise InputError("Cannot read a UTF-8 CSV manifest") from exc

    issues = []

    def issue(code, asset, detail):
        issues.append({"code": code, "path": asset, "detail": detail})

    if not rows:
        issue("empty_manifest", "", "Manifest has no assets")
    listed, seen_rows = {}, set()
    for line, row in enumerate(rows, 2):
        raw = (row.get("path") or "").strip()
        if None in row or any(row.get(field) is None for field in FIELDS):
            # Do not echo unvalidated paths, including in malformed rows.
            issue("invalid_row", "", "Malformed CSV row {}".format(line))
            continue
        pure = PurePosixPath(raw)
        if (not raw or pure.is_absolute() or "\\" in raw or ":" in raw
                or ".." in pure.parts or str(pure) != raw or raw == "."):
            issue("unsafe_path", "", "CSV row {}: use a normalized relative POSIX path inside the asset root".format(line))
            continue
        folded = raw.casefold()
        if folded in seen_rows:
            issue("duplicate_manifest_path", raw, "Duplicate or case-conflicting manifest path")
            continue
        seen_rows.add(folded)
        try:
            expected = tuple(int(row[field]) for field in FIELDS[1:])
            rate, channels, bits = expected
            if not (1 <= rate <= 384000 and 1 <= channels <= 32 and bits in (8, 16, 24, 32)):
                raise ValueError
        except (ValueError, TypeError):
            issue("invalid_metadata", raw, "Expected numeric sample rate, channel count and PCM bit depth")
            # Keep the path in the manifest inventory even if the metadata is bad.
            listed[raw] = None
        else:
            listed[raw] = expected

    files, seen_files = {}, {}
    def walk_error(_error):
        issue("scan_error", "", "Cannot enumerate part of the asset root")

    for parent, dirs, filenames in os.walk(root, followlinks=False, onerror=walk_error):
        dirs.sort()
        for name in list(dirs):
            p = Path(parent) / name
            if p.is_symlink():
                issue("symlink", p.relative_to(root).as_posix(), "Symlink directories are not followed")
                dirs.remove(name)
        for name in sorted(filenames):
            p = Path(parent) / name
            rel = p.relative_to(root).as_posix()
            try:
                mode = p.lstat().st_mode
            except OSError:
                issue("scan_error", rel, "Cannot inspect asset entry")
                continue
            if stat.S_ISLNK(mode):
                issue("symlink", rel, "Symlink files are not opened")
                continue
            if p.suffix.lower() != ".wav":
                continue
            if not stat.S_ISREG(mode):
                issue("non_regular_file", rel, "Only regular WAV files are opened; pipes, sockets and devices are skipped")
                continue
            if not NAME.fullmatch(name):
                issue("invalid_name", rel, "Use lowercase snake_case.wav")
            folded = rel.casefold()
            if folded in seen_files:
                issue("case_collision", rel, "Case-conflicting path: " + seen_files[folded])
            seen_files[folded] = rel
            files[rel] = p

    for rel in sorted(set(listed) - set(files)):
        issue("missing_file", rel, "Manifest path is not a regular WAV in the scanned inventory")
    for rel in sorted(set(files) - set(listed)):
        issue("unlisted_file", rel, "WAV is not present in the manifest")

    checked, content = 0, {}
    for rel, p in sorted(files.items()):
        try:
            info = p.lstat()
            if not stat.S_ISREG(info.st_mode):
                issue("non_regular_file", rel, "Asset is no longer a regular file")
                continue
            if info.st_size > max_bytes:
                issue("file_too_large", rel, "File exceeds configured byte limit")
                continue
            with wave.open(str(p), "rb") as audio:
                actual = (audio.getframerate(), audio.getnchannels(), audio.getsampwidth() * 8)
                if audio.getcomptype() != "NONE":
                    issue("unsupported_encoding", rel, "Only uncompressed PCM WAV is supported")
                    continue
                if audio.getnframes() == 0:
                    issue("empty_audio", rel, "WAV contains no audio frames")
                digest = hashlib.sha256(repr(actual).encode("ascii"))
                read_bytes = 0
                while True:
                    block = audio.readframes(8192)
                    if not block:
                        break
                    read_bytes += len(block)
                    digest.update(block)
                expected_bytes = audio.getnframes() * audio.getnchannels() * audio.getsampwidth()
                if read_bytes != expected_bytes:
                    issue("truncated_audio", rel, "PCM length disagrees with the WAV frame count")
                    continue
                checked += 1
                if listed.get(rel) is not None and actual != listed[rel]:
                    issue("metadata_mismatch", rel, "Expected {}, found {} (Hz, channels, bits)".format(listed[rel], actual))
                fingerprint = digest.hexdigest()
                if fingerprint in content:
                    issue("duplicate_audio", rel, "Identical PCM and format: " + content[fingerprint])
                else:
                    content[fingerprint] = rel
        except (OSError, wave.Error, EOFError) as exc:
            # Stable error categories avoid leaking local absolute paths in shared reports.
            issue("unreadable_wav", rel, "Cannot read PCM WAV ({})".format(type(exc).__name__))
    issues.sort(key=lambda item: (item["path"], item["code"]))
    return {"schema_version": 1, "assets_checked": checked,
            "error_count": len(issues), "issues": issues}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    args = parser.parse_args(argv)
    try:
        report = validate(args.root, args.manifest, args.max_bytes)
    except InputError as exc:
        if args.format == "json":
            print(json.dumps({"schema_version": 1, "input_error": str(exc)}))
        else:
            print("Input error: " + str(exc), file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("{} WAVs checked; {} issue(s)".format(report["assets_checked"], report["error_count"]))
        for item in report["issues"]:
            print("[{code}] {path}: {detail}".format(**item))
    return 1 if report["issues"] else 0


if __name__ == "__main__":
    sys.exit(main())
