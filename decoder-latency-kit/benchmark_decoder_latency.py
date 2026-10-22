"""Time one complete decode call per shot, using a CPU or custom decoder."""

import argparse
import csv
import importlib
import json
from pathlib import Path
import platform
import time

import numpy as np
import stim


def make_decoder(backend, model, metadata):
    if backend == "pymatching":
        import pymatching
        return pymatching.Matching.from_detector_error_model(model).decode
    module, separator, factory = backend.partition(":")
    if not separator:
        raise ValueError("backend must be pymatching or module:factory")
    decoder = getattr(importlib.import_module(module), factory)(model, metadata)
    if not callable(decoder):
        raise TypeError("Decoder factory must return a synchronous callable")
    return decoder


def check_prediction(predicted, observable_count):
    bits = np.asarray(predicted)
    if bits.shape != (observable_count,) or not np.all((bits == 0) | (bits == 1)):
        raise ValueError("Decoder must return one unpacked 0/1 bit per logical observable")
    return bits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--backend", default="pymatching", help="pymatching or importable module:factory")
    parser.add_argument("--shots", type=int, default=1000)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--warmup-shots", type=int, default=32)
    parser.add_argument("--timing-scope", default="synchronous decoder API call; excludes input loading, graph build, unpacking, and QPU transport")
    parser.add_argument("--raw-out", type=Path, help="Optional individual call durations")
    args = parser.parse_args()
    if min(args.shots, args.repeats, args.warmup_shots) < 1:
        parser.error("shots, repeats, and warmup-shots must be positive")
    env_path = args.out.with_suffix(".environment.json")
    outputs = [args.out, env_path] + ([args.raw_out] if args.raw_out else [])
    if len({p.resolve() for p in outputs}) != len(outputs) or any(p.exists() for p in outputs):
        parser.error("Output paths must be distinct and must not already exist")
    with (args.dataset / "manifest.csv").open() as file:
        cases = list(csv.DictReader(file))
    for path in outputs:
        path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["case", "mode", "batch_size", "shots", "repeats", "model_build_ms",
              "mean_call_us", "median_call_us", "p95_call_us", "p99_call_us",
              "shot_failures_first_repeat", "timing_scope"]
    raw_file = args.raw_out.open("w", newline="") if args.raw_out else None
    try:
        raw_writer = csv.writer(raw_file) if raw_file else None
        if raw_writer:
            raw_writer.writerow(["case", "repeat", "shot", "decode_us"])
        with args.out.open("w", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=fields)
            writer.writeheader()
            for index, case in enumerate(cases):
                folder = args.dataset / case["case"]
                metadata = json.loads((folder / "metadata.json").read_text())
                model = stim.DetectorErrorModel.from_file(folder / "decoder.dem")
                start = time.perf_counter_ns()
                decode = make_decoder(args.backend, model, metadata)
                build_ms = (time.perf_counter_ns() - start)/1e6
                n = min(args.shots, int(case["shots"]))
                det_width = (model.num_detectors+7)//8
                obs_width = (model.num_observables+7)//8
                packed = np.fromfile(folder / "detections.b8", dtype=np.uint8,
                                     count=n*det_width).reshape(n, det_width)
                actual_packed = np.fromfile(folder / "observables.b8", dtype=np.uint8,
                                            count=n*obs_width).reshape(n, obs_width)
                events = np.unpackbits(packed, axis=1, bitorder="little")[:, :model.num_detectors]
                actual = np.unpackbits(actual_packed, axis=1, bitorder="little")[:, :model.num_observables]
                for shot in range(min(args.warmup_shots, n)):
                    check_prediction(decode(events[shot]), model.num_observables)
                times = []
                failures = 0
                for repeat in range(args.repeats):
                    for shot in range(n):
                        syndrome = events[shot]
                        start = time.perf_counter_ns()
                        prediction = decode(syndrome)
                        elapsed = (time.perf_counter_ns()-start)/1000
                        times.append(elapsed)
                        bits = check_prediction(prediction, model.num_observables)
                        if repeat == 0:
                            failures += int(np.any(bits != actual[shot]))
                        if raw_writer:
                            raw_writer.writerow([case["case"], repeat, shot, elapsed])
                writer.writerow({"case": case["case"], "mode": "decode_unpacked_single", "batch_size": 1,
                    "shots": n, "repeats": args.repeats, "model_build_ms": build_ms,
                    "mean_call_us": float(np.mean(times)), "median_call_us": float(np.median(times)),
                    "p95_call_us": float(np.percentile(times, 95)), "p99_call_us": float(np.percentile(times, 99)),
                    "shot_failures_first_repeat": failures, "timing_scope": args.timing_scope})
                file.flush()
                print(f"{index+1}/{len(cases)} {case['case']}", flush=True)
    finally:
        if raw_file:
            raw_file.close()
    environment = {"platform": platform.platform(), "python": platform.python_version(),
        "stim": stim.__version__, "numpy": np.__version__, "backend": args.backend,
        "timer": "perf_counter_ns", "timing_scope": args.timing_scope,
        "shots_requested": args.shots, "repeats": args.repeats,
        "warmup_shots": args.warmup_shots, "cases": len(cases),
        "dataset": str(args.dataset.resolve()), "complete": True,
        "scope": "One independent full experiment per decode; representative surgery dataset, not exact SWIPER interior windows"}
    if args.backend == "pymatching":
        import pymatching
        environment["pymatching"] = pymatching.__version__
    env_path.write_text(json.dumps(environment, indent=2)+"\n")


if __name__ == "__main__":
    main()
