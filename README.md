# Game Audio Asset Validator

独立 Python 工程工具样例 / Independent offline Python tooling sample.

采用 MIT 许可证的独立公开工程样例。在 AI 辅助下独立编写，不读取产品工程，不使用真实游戏素材，也不是已有商业工具的代码摘录。

## Quick start

Python 3.9+; standard library only. No package installation, network, Wwise or Unity is required. Run from this directory:

```sh
python3 -m unittest discover -s tests -v
python3 make_demo.py demo-valid
python3 asset_validator.py demo-valid/assets demo-valid/manifest.csv
python3 make_demo.py demo-invalid --invalid
python3 asset_validator.py demo-invalid/assets demo-invalid/manifest.csv --format json
```

The last command intentionally exits with code 1 and reports four issues: an invalid name, a metadata mismatch, a missing file and duplicate audio. Fixture generation writes only to a new destination directory and refuses to overwrite one. Use a different destination to rerun it. The validator itself only reads input and writes its report to stdout/stderr; it never renames, repairs or deletes assets. It does not play sound.

## Manifest and checks

The manifest must be a regular, non-symlink file. CSV uses UTF-8 (optional BOM), exactly this header/order:

```csv
path,sample_rate,channels,bit_depth
ui_confirm.wav,48000,1,16
```

- Relative normalized paths, e.g. `ui/ui_confirm.wav`; no absolute paths, backslashes, `..` or symlinks.
- Lowercase snake_case WAV filenames. Directory names are not subject to the filename convention.
- Manifest duplicates (case-insensitive), missing and unlisted WAVs.
- PCM sample rate, channel count and bit depth versus the manifest.
- Corrupt, truncated, empty or oversized WAV files.
- Special files such as pipes, sockets and devices are rejected before opening. A pipe supplied as the manifest is also rejected.
- Exact duplicate decoded PCM bytes with identical format. This is **not** a perceptual similarity detector.

All findings are errors for this demonstration policy. Settings are generic sample rules, not a studio's production rules. Non-WAV regular files are ignored. Files are hashed in small chunks with a default 128 MiB per-file limit (`--max-bytes` to change it). Reports are deterministically sorted. Unsafe or malformed manifest rows leave the report's `path` blank and identify the CSV row instead; unvalidated path text is never echoed. Other findings retain asset-relative names, so review those names before sharing a report. This is not a general-purpose personal-data redactor.

Exit codes: **0** valid, **1** asset findings, **2** invalid arguments/unusable manifest. JSON includes `schema_version: 1`; invocation errors use `input_error` instead of an asset report.

## Deliberate boundaries

Supports uncompressed integer PCM WAV understood by the installed Python `wave` module. Float WAV, compressed formats and WAVE_FORMAT_EXTENSIBLE support are outside this sample's portable contract. There is no loudness, peak, loop, musical or perceptual analysis. Exact duplicate detection does not resample or normalize audio.

This is for trusted, stable local directories. Symlink checks reduce accidental traversal, but are not a security sandbox against concurrent filesystem changes; do not run it on an adversarial or actively changing directory. Large manifests/file counts can still consume memory. A production system would need a more comprehensive ingestion and resource-limit design.

## Synthetic material and publication

`make_demo.py` synthesizes short low-amplitude sine waves with Python math; filenames are fictional. No product recordings, proprietary naming rules, customer paths or credentials are included. `examples/manifest.example.csv` illustrates the schema only; generate the matching audio using the command above.

Review the code and be prepared to explain trade-offs before using it in an interview. This is an independent sample, not a claim of released game-engine integration. Verified on macOS arm64 with Python 3.9.6: 25 unit tests passed with no skips. Other operating systems and Python versions have not been tested.

## License and attribution

Copyright (c) 2026 Crescent Feng (冯新月). Licensed under the [MIT License](LICENSE).

Developed with AI assistance as an independent engineering example. The MIT license applies to the code, tests, documentation and example CSV supplied in this example project; it does not grant rights to separate commercial products, private repositories or portfolio materials not included here. This scope clarification adds no restrictions to the MIT-licensed example itself.
