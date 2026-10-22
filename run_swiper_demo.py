"""Compare SWIPER scheduling with/without speculation; no fidelity model."""

import argparse
import json
import math
from pathlib import Path

from swiper.lattice_surgery_schedule import LatticeSurgerySchedule
from swiper.schedule_experiments import RegularTSchedule
from swiper.simulator import DecodingSimulator
from swiper_latency import load_latency_fn


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round-us", type=float, default=1.0)
    timing = parser.add_mutually_exclusive_group()
    timing.add_argument("--decode-us", type=float, default=14.0,
                        help="Fixed per-window latency in microseconds")
    timing.add_argument("--latency-profile", type=Path,
                        help="Measured/surrogate JSON latency profile")
    parser.add_argument("--latency-statistic", choices=["mean", "median", "p95", "p99"], default="median")
    parser.add_argument("--job-overhead-us", type=float, default=0,
                        help="Additional time charged to every decoding job")
    parser.add_argument("--decoders", type=int, default=4)
    parser.add_argument("--distance", type=int, default=None,
                        help="Defaults to profile distance, or 7 for fixed latency")
    parser.add_argument("--schedule", type=Path, help="Optional compiled .lss schedule")
    parser.add_argument("--method", choices=["sliding", "parallel", "aligned"], default="aligned")
    parser.add_argument("--t-gates", type=int, default=10)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    if args.distance is None:
        args.distance = json.loads(args.latency_profile.read_text())["distance"] if args.latency_profile else 7
    if (min(args.round_us, args.decode_us, args.decoders, args.distance, args.t_gates) <= 0
            or not all(math.isfinite(x) for x in [args.round_us, args.decode_us, args.job_overhead_us])
            or args.job_overhead_us < 0):
        parser.error("Timing, decoder count, distance, and gate count must be positive")

    latency_rounds = math.ceil((args.decode_us + args.job_overhead_us) / args.round_us)

    def decode_latency(_volume):
        # The upstream API receives only spacetime volume, not syndrome bits.
        return latency_rounds

    if args.latency_profile:
        decode_latency = load_latency_fn(args.latency_profile, round_us=args.round_us,
            distance=args.distance, statistic=args.latency_statistic,
            per_job_overhead_us=args.job_overhead_us)
    requested_volumes = set()

    def tracked_decode_latency(volume):
        requested_volumes.add(volume)
        return decode_latency(volume)

    results = []
    for speculate in (False, True):
        success, _, device, _, decoder = DecodingSimulator().run(
            schedule=(LatticeSurgerySchedule.from_str(args.schedule.read_text().strip())
                      if args.schedule else RegularTSchedule(args.t_gates, idle_between_Ts=0).schedule),
            distance=args.distance,
            scheduling_method=args.method,
            decoding_latency_fn=tracked_decode_latency,
            speculation_mode="integrated" if speculate else None,
            speculation_latency=1,
            speculation_accuracy=0.9 if speculate else 0.0,
            max_parallel_processes=args.decoders,
            rng=args.seed,
            device_rounds_cutoff=100_000,
            pending_window_count_cutoff=10_000,
        )
        if not success:
            raise RuntimeError("Simulation reached a cutoff; reduce latency or increase the pool")
        results.append({
            "speculation": speculate,
            "device_rounds": device.num_rounds,
            "device_time_us": device.num_rounds * args.round_us,
            "decode_completion_round": decoder.num_rounds,
            "decode_completion_time_us": decoder.num_rounds * args.round_us,
            "unwanted_idle_patch_rounds": sum(
                sr.is_unwanted_idle
                for rounds in device.generated_syndrome_data for sr in rounds
            ),
            "mean_conditioned_wait_rounds": device.avg_conditioned_decode_wait_time,
            "peak_active_decoders": decoder.max_parallel_decoders,
            "completed_windows": decoder.num_completed_windows,
            "failed_speculations": decoder.num_failed_speculations,
        })
    print(json.dumps({
        "configuration": {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
        "latency_model": decode_latency.model.description() if args.latency_profile else {"fixed_rounds": latency_rounds},
        "modeled_decode_latency_rounds": None if args.latency_profile else latency_rounds,
        "observed_volume_latencies": [{"patch_rounds": v, "decoder_rounds": decode_latency(v)} for v in sorted(requested_volumes)],
        "notes": [
            "Timing simulation only; no logical error rate or operation fidelity is computed.",
            "Unwanted idle patch-rounds sum over patches, not elapsed wall-clock rounds.",
            "Speculation accuracy is an input probability, not decoder fidelity.",
        ],
        "results": results,
    }, indent=2))


if __name__ == "__main__":
    main()
