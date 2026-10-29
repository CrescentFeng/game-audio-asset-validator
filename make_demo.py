"""Create synthetic fixtures in a NEW directory; never overwrite a collection."""
import argparse
import csv
import math
from pathlib import Path
import struct
import wave


def write_tone(path, frequency, rate=48000, channels=1):
    """Short low-amplitude mathematical tone, not a recording or product sound."""
    with wave.open(str(path), "wb") as out:
        out.setparams((channels, 2, rate, 0, "NONE", "not compressed"))
        frames = []
        for i in range(rate // 20):
            value = int(1000 * math.sin(2 * math.pi * frequency * i / rate))
            frames.append(struct.pack("<h", value) * channels)
        out.writeframes(b"".join(frames))


def generate(destination, invalid=False):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    assets = destination / "assets"
    assets.mkdir()
    write_tone(assets / "ui_confirm.wav", 440)
    write_tone(assets / "ui_cancel.wav", 660)
    rows = [["ui_confirm.wav", 48000, 1, 16], ["ui_cancel.wav", 48000, 1, 16]]
    if invalid:
        write_tone(assets / "ui_copy.wav", 440)
        write_tone(assets / "Bad Name.wav", 880, rate=44100)
        rows += [["ui_copy.wav", 48000, 1, 16], ["Bad Name.wav", 48000, 1, 16],
                 ["missing.wav", 48000, 1, 16]]
    with (destination / "manifest.csv").open("w", encoding="utf-8", newline="") as out:
        writer = csv.writer(out)
        writer.writerow(["path", "sample_rate", "channels", "bit_depth"])
        writer.writerows(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--invalid", action="store_true")
    args = parser.parse_args()
    try:
        generate(args.destination, args.invalid)
    except FileExistsError:
        parser.exit(2, "Destination exists; choose a new directory. Nothing was overwritten.\n")
