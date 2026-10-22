import csv
import json
import math
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from fit_decoder_latency import fit_profile, monotone_fit
from swiper_latency import LatencyProfile, load_latency_fn
from swiper.schedule_experiments import RegularTSchedule
from swiper.simulator import DecodingSimulator


def profile(a=2.1, b=.5):
    return {"schema_version": 1, "input_unit": "patch_rounds", "output_unit": "microseconds",
            "distance": 3, "fits": {"median": {"fixed_us": a, "us_per_patch_round": b}}}


def test_callback_units_distance_and_rounding(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile()))
    callback = load_latency_fn(path, round_us=2, distance=3, per_job_overhead_us=.3)
    assert callback(1) == 2
    assert callback(np.int64(9)) == 4
    assert callback.model.latency_us(9) == pytest.approx(6.9)
    assert [callback(v) for v in range(1, 30)] == sorted(callback(v) for v in range(1, 30))
    with pytest.raises(ValueError, match="distance"):
        load_latency_fn(path, round_us=1, distance=7)
    with pytest.raises(ValueError, match="Statistic"):
        load_latency_fn(path, round_us=1, distance=3, statistic="p95")


@pytest.mark.parametrize("volume", [0, -1, 1.5, "3", True])
def test_invalid_volumes_rejected(volume):
    model = LatencyProfile(profile(), round_us=1, distance=3)
    with pytest.raises(ValueError):
        model.latency_rounds(volume)


def test_positive_countdown_and_bad_coefficients():
    assert LatencyProfile(profile(0, 0), round_us=1, distance=3).latency_rounds(3) == 1
    for value in [-1, math.inf, math.nan]:
        with pytest.raises(ValueError):
            LatencyProfile(profile(value, 1), round_us=1, distance=3)
        with pytest.raises(ValueError):
            LatencyProfile(profile(), round_us=value, distance=3)


def test_monotone_fit_preserves_local_cost_and_pools_decreasing_points():
    fit = monotone_fit([1, 3, 10], [5, 11, 32])
    assert [p["latency_us"] for p in fit["knots"]] == pytest.approx([5, 11, 32])
    decreasing = monotone_fit([1, 3, 10], [9, 8, 7])
    assert [p["latency_us"] for p in decreasing["knots"]] == [8, 8, 8]
    weighted = monotone_fit([1, 1, 3], [10, 8, 3])
    assert [p["latency_us"] for p in weighted["knots"]] == [7, 7]
    with pytest.raises(ValueError):
        monotone_fit([1, 2], [math.nan, 3])


def write_fit_inputs(tmp_path, *, missing=False, batch_only=False):
    manifest = tmp_path / "manifest.csv"
    timings = tmp_path / "timings.csv"
    cases = [("a", 1, 3, "s1"), ("b", 2, 5, "s2"), ("c", 1, 14, "s1")]
    with manifest.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["case", "distance", "patches", "rounds", "shape_id"])
        writer.writerows((name, 3, patches, rounds, shape) for name, patches, rounds, shape in cases)
    with timings.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["case", "mode", "batch_size", "median_call_us", "p95_call_us", "timing_scope"])
        for name, patches, rounds, shape in cases[:-1] if missing else cases:
            if not batch_only:
                writer.writerow([name, "decode_unpacked_single", 1, 2+3*patches*rounds, 4+5*patches*rounds, "test"])
            writer.writerow([name, "decode_batch_packed", 32, .001, .002, "test"])
    return timings, manifest


def test_fit_filters_batch_times_and_checks_coverage(tmp_path):
    timings, manifest = write_fit_inputs(tmp_path)
    result = fit_profile(timings, manifest)
    assert result["measured_cases"] == 3
    assert [p["latency_us"] for p in result["fits"]["median"]["knots"]] == pytest.approx([11, 32, 44])
    assert [p["latency_us"] for p in result["fits"]["p95"]["knots"]] == pytest.approx([19, 54, 74])
    model = LatencyProfile(result, round_us=1, distance=3)
    assert model.latency_us(1) == pytest.approx(11)
    assert model.latency_us(5) == pytest.approx(17)
    assert model.latency_us(28) == pytest.approx(88)
    assert result["exact_swiper_window_calibration"] is False
    timings, manifest = write_fit_inputs(tmp_path, missing=True)
    with pytest.raises(ValueError, match="Missing"):
        fit_profile(timings, manifest)
    assert len(fit_profile(timings, manifest, allow_partial=True)["missing_cases"]) == 1
    timings, manifest = write_fit_inputs(tmp_path, batch_only=True)
    with pytest.raises(ValueError, match="batch"):
        fit_profile(timings, manifest)


def test_custom_hardware_adapter_to_profile_cli(tmp_path):
    root = Path(__file__).resolve().parent
    source = root / "tests/fixtures/decoder_cases"
    with (source / "manifest.csv").open() as file:
        reader = csv.DictReader(file)
        fields = reader.fieldnames
        cases = [next(reader), next(reader)]
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "cases").symlink_to(source / "cases", target_is_directory=True)
    with (dataset / "manifest.csv").open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(cases)
    (tmp_path / "test_backend.py").write_text(
        "import pymatching\ndef build(model, metadata):\n"
        "    assert metadata['distance'] == 3\n"
        "    return pymatching.Matching.from_detector_error_model(model).decode\n")
    env = {**os.environ, "PYTHONPATH": str(tmp_path)}
    timings, output = tmp_path / "timings.csv", tmp_path / "profile.json"
    subprocess.run([sys.executable, str(root / "benchmark_decoder_latency.py"), str(dataset),
                    "--out", str(timings), "--shots", "16", "--backend", "test_backend:build"],
                   env=env, check=True, capture_output=True, text=True)
    subprocess.run([sys.executable, str(root / "fit_decoder_latency.py"),
                    "--timings", str(timings), "--manifest", str(dataset / "manifest.csv"),
                    "--out", str(output)], check=True, capture_output=True, text=True)
    callback = load_latency_fn(output, round_us=1, distance=3)
    assert callback(1) >= 1 and callback(100) >= callback(1)
    assert callback.model.profile["provenance"]["environment"]["backend"] == "test_backend:build"


def test_explicit_region_volume_overrides_full_circuit_proxy(tmp_path):
    timings, manifest = write_fit_inputs(tmp_path)
    with manifest.open() as file:
        rows = list(csv.DictReader(file))
    for index, row in enumerate(rows):
        row["total_patch_rounds"] = index+1
    with manifest.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    result = fit_profile(timings, manifest)
    assert result["measured_proxy_volumes"] == [1, 2, 3]
    assert result["volume_proxy_definition"].startswith("Explicit total_patch_rounds")


@pytest.mark.parametrize("method", ["sliding", "parallel", "aligned"])
@pytest.mark.parametrize("speculate", [False, True])
def test_callback_runs_in_real_swiper(tmp_path, method, speculate):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile(1, .25)))
    callback = load_latency_fn(path, round_us=1, distance=3)
    requested = []
    def tracked(volume):
        requested.append(volume)
        return callback(volume)
    success, _, device, _, decoder = DecodingSimulator().run(
        schedule=RegularTSchedule(3, idle_between_Ts=0).schedule, distance=3,
        scheduling_method=method, decoding_latency_fn=tracked,
        speculation_mode="integrated" if speculate else None,
        speculation_latency=1, speculation_accuracy=.9 if speculate else 0,
        max_parallel_processes=2, rng=0, device_rounds_cutoff=10000,
        pending_window_count_cutoff=1000)
    assert success and requested and decoder.num_completed_windows > 0
    assert decoder.max_parallel_decoders <= 2 and device.num_rounds > 0
    assert all(callback(v) == max(1, math.ceil((1+.25*v)/1)) for v in requested)
