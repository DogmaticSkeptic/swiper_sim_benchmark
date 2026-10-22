"""Fit an explicitly approximate monotone SWIPER latency profile."""

import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np
from swiper_latency import predict_piecewise_us

STAT_COLUMNS = {"mean": "mean_call_us", "median": "median_call_us", "p95": "p95_call_us", "p99": "p99_call_us"}


def monotone_fit(volumes, times):
    x, y = np.asarray(volumes, dtype=float), np.asarray(times, dtype=float)
    if x.ndim != 1 or y.shape != x.shape or len(x) < 2 or len(set(x)) < 2:
        raise ValueError("At least two distinct measured volumes are required")
    if not np.isfinite(x).all() or not np.isfinite(y).all() or (x <= 0).any() or (y <= 0).any():
        raise ValueError("Volumes and timings must be finite and positive")
    unique = sorted(set(x))
    blocks = []
    for index, volume in enumerate(unique):
        observations = y[x == volume]
        blocks.append([index, index+1, len(observations), float(sum(observations))])
        while len(blocks) >= 2 and blocks[-2][3]/blocks[-2][2] > blocks[-1][3]/blocks[-1][2]:
            right, left = blocks.pop(), blocks.pop()
            blocks.append([left[0], right[1], left[2]+right[2], left[3]+right[3]])
    fitted = [None]*len(unique)
    for start, end, weight, total in blocks:
        fitted[start:end] = [total/weight]*(end-start)
    knots = [{"volume": int(v), "latency_us": float(t)} for v, t in zip(unique, fitted)]
    predicted = np.array([predict_piecewise_us(knots, v) for v in x])
    return {"knots": knots,
            "case_rmse_us": float(np.sqrt(np.mean((predicted-y)**2))),
            "case_median_absolute_relative_error": float(np.median(np.abs(predicted-y)/y))}


def fit_profile(timing_path, manifest_path, *, allow_partial=False):
    with manifest_path.open() as file:
        manifest = {row["case"]: row for row in csv.DictReader(file)}
    with timing_path.open() as file:
        rows = [r for r in csv.DictReader(file)
                if r["mode"] == "decode_unpacked_single" and int(r["batch_size"]) == 1]
    if not rows:
        raise ValueError("No single-job timings found; batch amortized times cannot calibrate independent decoder slots")
    case_ids = [r["case"] for r in rows]
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("Duplicate single-job timing rows for the same case")
    if not set(case_ids) <= manifest.keys():
        raise ValueError("Timing rows reference unknown dataset cases")
    missing = sorted(manifest.keys()-set(case_ids))
    if missing and not allow_partial:
        raise ValueError(f"Missing {len(missing)} case timings; use --allow-partial only deliberately")
    distances = {int(manifest[c]["distance"]) for c in case_ids}
    if len(distances) != 1:
        raise ValueError("Use a separate latency profile for each distance")
    scopes = {r["timing_scope"] for r in rows}
    if len(scopes) != 1:
        raise ValueError("Cannot combine different timing scopes in one fit")
    explicit_volumes = [bool(manifest[c].get("total_patch_rounds")) for c in case_ids]
    if any(explicit_volumes) and not all(explicit_volumes):
        raise ValueError("Provide total_patch_rounds for every case or for none")
    if all(explicit_volumes):
        volumes = [int(manifest[c]["total_patch_rounds"]) for c in case_ids]
        volume_definition = "Explicit total_patch_rounds supplied in the manifest; should sum commit and buffer durations"
    else:
        volumes = [int(manifest[c]["patches"])*int(manifest[c]["rounds"]) for c in case_ids]
        volume_definition = "occupied tile count times full circuit measurement rounds; includes preparation and surgery context"
    fits = {}
    for statistic, column in STAT_COLUMNS.items():
        if not all(row.get(column) for row in rows):
            continue
        times = [float(row[column]) for row in rows]
        fit = monotone_fit(volumes, times)
        # Hold out whole spatial masks so repeats of one geometry cannot leak
        # into its own validation. This tests the surrogate, not physical fidelity.
        groups = [manifest[c]["shape_id"] for c in case_ids]
        errors = []
        for group in set(groups):
            train = [i for i, g in enumerate(groups) if g != group]
            test = [i for i, g in enumerate(groups) if g == group]
            if len({volumes[i] for i in train}) < 2:
                continue
            held_fit = monotone_fit([volumes[i] for i in train], [times[i] for i in train])
            errors.extend(abs(predict_piecewise_us(held_fit["knots"], volumes[i])-times[i])/times[i] for i in test)
        fit["held_out_footprint_median_absolute_relative_error"] = float(np.median(errors)) if errors else None
        fits[statistic] = fit
    if "median" not in fits:
        raise ValueError("median_call_us is required")
    environment_path = timing_path.with_suffix(".environment.json")
    environment = json.loads(environment_path.read_text()) if environment_path.exists() else None
    return {"schema_version": 1, "input_unit": "patch_rounds", "output_unit": "microseconds",
        "distance": distances.pop(), "model": "monotone_piecewise_linear",
        "calibration_scope": "representative_surgery_volume_surrogate",
        "exact_swiper_window_calibration": False,
        "volume_proxy_definition": volume_definition,
        "fit_method": "Weighted isotonic least squares at distinct proxy volumes; equal weight per case; linear interpolation between knots",
        "extrapolation": "Below range: clamp to lowest fitted cost; above range: scale proportionally from the last knot",
        "fits": fits, "measured_cases": len(rows), "missing_cases": missing,
        "measured_spatial_masks": len({manifest[c]["shape_id"] for c in case_ids}),
        "measured_proxy_volume_range": [min(volumes), max(volumes)],
        "measured_proxy_volumes": sorted(set(volumes)), "timing_scope": scopes.pop(),
        "provenance": {"timings_sha256": hashlib.sha256(timing_path.read_bytes()).hexdigest(),
                       "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                       "environment": environment},
        "limitations": ["Geometry, commit/buffer roles, and temporal boundary messages are not callback inputs.",
            "Representative full-circuit timings do not establish exact SWIPER window calibration.",
            "The monotone curve interpolates and extrapolates; it is not a measured lookup for every volume.",
            "A deterministic fitted p95 curve is a sensitivity scenario, not a stochastic tail-latency simulation.",
            "Only measurements on the intended decoder/hardware calibrate that target."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timings", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()
    if args.out.exists():
        parser.error("Output exists; choose a new profile path")
    try:
        profile = fit_profile(args.timings, args.manifest, allow_partial=args.allow_partial)
    except (ValueError, KeyError) as exc:
        parser.error(str(exc))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(profile, indent=2, allow_nan=False)+"\n")
    summary = {k: profile[k] for k in ["measured_cases", "measured_spatial_masks", "measured_proxy_volume_range"]}
    summary["fits"] = {stat: {"knots": len(fit["knots"]), **{k: v for k, v in fit.items() if k != "knots"}}
                       for stat, fit in profile["fits"].items()}
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
