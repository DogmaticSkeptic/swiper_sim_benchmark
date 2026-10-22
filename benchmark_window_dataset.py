"""Reference CPU decoder benchmark; graph construction and I/O are timed separately."""

import argparse
import csv
import fnmatch
import json
from pathlib import Path
import platform
import time

import numpy as np
import pymatching
import stim


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, nargs="?", default=Path(__file__).resolve().parent)
    parser.add_argument("--case", default="*", help="Glob matched against case directory name")
    parser.add_argument("--shots", type=int, default=1000, help="Maximum shots timed per case and repetition")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--batch-sizes", type=int, nargs="+", default=[1, 32, 256])
    parser.add_argument("--out", type=Path, default=Path("decoder-timing.csv"))
    args = parser.parse_args()
    if min(args.shots, args.repeats, *args.batch_sizes) < 1:
        parser.error("shots, repeats, and batch sizes must be positive")
    if args.out.exists() or args.out.with_suffix(".environment.json").exists():
        parser.error("Timing output exists; choose a new --out")
    with (args.dataset / "manifest.csv").open() as file:
        cases = [row for row in csv.DictReader(file) if fnmatch.fnmatch(Path(row["case"]).name, args.case)]
    if not cases:
        parser.error("No cases matched")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fields = ["case", "mode", "batch_size", "shots", "repeats", "model_build_ms",
              "median_call_us", "p95_call_us", "p99_call_us", "median_amortized_us_per_shot",
              "shot_failures_first_repeat", "timing_scope"]
    with args.out.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for case in cases:
            folder = args.dataset / case["case"]
            meta = json.loads((folder / "metadata.json").read_text())
            model = stim.DetectorErrorModel.from_file(folder / "decoder.dem")
            start = time.perf_counter_ns()
            matching = pymatching.Matching.from_detector_error_model(model)
            build_ms = (time.perf_counter_ns() - start) / 1e6
            n = min(args.shots, int(case["shots"]))
            det_width = meta["files"]["detections.b8"]["bytes_per_shot"]
            obs_width = meta["files"]["observables.b8"]["bytes_per_shot"]
            packed = np.fromfile(folder / "detections.b8", dtype=np.uint8,
                                 count=n * det_width).reshape(n, det_width)
            actual = np.fromfile(folder / "observables.b8", dtype=np.uint8,
                                 count=n * obs_width).reshape(n, obs_width)
            # Ordinary decode() takes unpacked events. Preprocess before timing.
            unpacked = np.unpackbits(packed, axis=1, bitorder="little")[:, :model.num_detectors]
            actual_bits = np.unpackbits(actual, axis=1, bitorder="little")[:, :model.num_observables]
            matching.decode(unpacked[0])
            matching.decode_batch(packed[:min(32, n)], bit_packed_shots=True, bit_packed_predictions=True)
            for batch_size in sorted(set(args.batch_sizes)):
                if batch_size > n:
                    continue
                times = []
                failures = 0
                usable = n // batch_size * batch_size
                for repeat in range(args.repeats):
                    for index in range(0, usable, batch_size):
                        # Slice views outside the timed call.
                        events = unpacked[index] if batch_size == 1 else packed[index:index+batch_size]
                        start = time.perf_counter_ns()
                        if batch_size == 1:
                            predicted = matching.decode(events)
                        else:
                            predicted = matching.decode_batch(events, bit_packed_shots=True,
                                                              bit_packed_predictions=True)
                        times.append((time.perf_counter_ns() - start) / 1000)
                        if repeat == 0:
                            if batch_size == 1:
                                failures += int(np.any(predicted != actual_bits[index]))
                            else:
                                failures += int(np.count_nonzero(np.any(
                                    predicted != actual[index:index+batch_size], axis=1)))
                median = float(np.median(times))
                writer.writerow({"case": case["case"],
                    "mode": "decode_unpacked_single" if batch_size == 1 else "decode_batch_packed",
                    "batch_size": batch_size, "shots": usable, "repeats": args.repeats,
                    "model_build_ms": build_ms, "median_call_us": median,
                    "p95_call_us": float(np.percentile(times, 95)),
                    "p99_call_us": float(np.percentile(times, 99)),
                    "median_amortized_us_per_shot": median / batch_size,
                    "shot_failures_first_repeat": failures,
                    "timing_scope": "CPU Python API call; excludes I/O, graph build, preprocessing, and transport"})
                file.flush()
            print(case["case"], flush=True)
    args.out.with_suffix(".environment.json").write_text(json.dumps({
        "platform": platform.platform(), "python": platform.python_version(),
        "stim": stim.__version__, "pymatching": pymatching.__version__, "numpy": np.__version__,
        "timer": "perf_counter_ns", "cases": len(cases),
        "dataset": str(args.dataset.resolve()), "shots_requested": args.shots,
        "repeats": args.repeats, "batch_sizes": args.batch_sizes,
        "note": "Batch amortized time is throughput, not individual-shot response latency. Timing includes Python/native-call overhead."
    }, indent=2) + "\n")


if __name__ == "__main__":
    main()
