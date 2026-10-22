"""Synchronize the portable kit with project source and build its shareable archive."""

import gzip
import hashlib
from pathlib import Path
import shutil
import tarfile

ROOT = Path(__file__).resolve().parent
KIT = ROOT / "decoder-latency-kit"
FILES = ["README.md", "swiper_latency.py", "benchmark_decoder_latency.py",
         "fit_decoder_latency.py", "profiles/cpu-pymatching-d3.json",
         "reference-single-job.csv", "reference-single-job.environment.json",
         "source-manifest.csv", "reference-volume-predictions.csv", "requirements.txt",
         "validation.json", "demo-cpu-reference.json", "demo-toffoli-reference.json"]


def main():
    for source, target in [
        ("swiper_latency.py", "swiper_latency.py"),
        ("benchmark_decoder_latency.py", "benchmark_decoder_latency.py"),
        ("fit_decoder_latency.py", "fit_decoder_latency.py"),
        ("decoder_latency_readme.md", "README.md"),
    ]:
        shutil.copyfile(ROOT / source, KIT / target)
    (KIT / "checksums.sha256").write_text("".join(
        f"{hashlib.sha256((KIT / name).read_bytes()).hexdigest()}  {name}\n"
        for name in sorted(FILES)))

    def portable_metadata(info):
        info.uid = info.gid = info.mtime = 0
        info.uname = info.gname = ""
        info.mode = 0o644
        return info

    archive = ROOT / "decoder-latency-kit.tar.gz"
    with archive.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as tar:
            for name in sorted(FILES + ["checksums.sha256"]):
                tar.add(KIT / name, arcname=f"decoder-latency-kit/{name}",
                        recursive=False, filter=portable_metadata)
    archive.with_suffix(archive.suffix + ".sha256").write_text(
        f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n")
    print(f"Synchronized kit and wrote {archive.name} ({archive.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
