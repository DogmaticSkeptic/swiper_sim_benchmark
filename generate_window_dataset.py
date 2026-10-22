"""Build paired Stim memory baselines for SWIPER's observed spatial masks.

Each occupied tile is an independent rotated-code patch. These are deliberately
not claimed to be coupled lattice-surgery circuits or interior decoding windows.
"""

import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil
import time

import numpy as np
import pymatching
import stim

ROOT = Path(__file__).resolve().parent
NOISE_KEYS = (
    "after_clifford_depolarization", "before_round_data_depolarization",
    "before_measure_flip_probability", "after_reset_flip_probability",
)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compose_patches(base, patches, distance, rounds):
    """Interleave identical patches instruction-by-instruction, preserving recs.

    Measurement and detector IDs are mapped explicitly; concatenating independent
    patch circuits would give misleading packet times and measurement ordering.
    """
    patches = sorted(map(tuple, patches))
    circuit = stim.Circuit()
    measurement_maps = [[] for _ in patches]
    detector_maps = [[] for _ in patches]
    packets = []
    measurements = detectors = 0
    stride = base.num_qubits
    for instruction in base.flattened():
        name = instruction.name
        if name == "TICK":
            circuit.append(instruction)
            continue
        if name == "SHIFT_COORDS":
            raise ValueError("Expected absolute coordinates after flattening")
        is_measurement = instruction.num_measurements > 0
        if is_measurement:
            if name not in {"MR", "MRX", "MRY", "M", "MX", "MY"}:
                raise ValueError(f"Unsupported measurement gate: {name}")
            final = name in {"M", "MX", "MY"}
            packet = {
                "packet_index": len(packets),
                "kind": "final_data_readout" if final else "syndrome_round",
                "measurement_range": [measurements, None],
                "measured_qubits": [], "detector_indices": [],
            }
            packets.append(packet)
        for patch_id, (row, col) in enumerate(patches):
            targets = []
            for target in instruction.targets_copy():
                if target.is_measurement_record_target:
                    local_index = len(measurement_maps[patch_id]) + target.value
                    if local_index < 0:
                        raise ValueError("Invalid measurement record")
                    targets.append(stim.target_rec(measurement_maps[patch_id][local_index] - measurements))
                elif target.is_qubit_target:
                    q = target.value + patch_id * stride
                    targets.append(stim.target_inv(q) if target.is_inverted_result_target else stim.GateTarget(q))
                else:
                    raise ValueError(f"Unsupported generated target: {target}")
            args = instruction.gate_args_copy()
            if name in {"QUBIT_COORDS", "DETECTOR"}:
                # Display coordinates use a margin between independent modules;
                # nominal SWIPER dimensions are recorded separately in metadata.
                args[0] += col * 2 * (distance + 1)
                args[1] += row * 2 * (distance + 1)
            elif name == "OBSERVABLE_INCLUDE":
                args[0] += patch_id * base.num_observables
            circuit.append(name, targets, args)
            if is_measurement:
                count = instruction.num_measurements
                measurement_maps[patch_id].extend(range(measurements, measurements + count))
                measurements += count
                packet["measured_qubits"].extend(target.value for target in targets)
            elif name == "DETECTOR":
                detector_maps[patch_id].append(detectors)
                # Generated memory detectors depend on the most recent packet.
                packets[-1]["detector_indices"].append(detectors)
                detectors += 1
        if is_measurement:
            packet["measurement_range"][1] = measurements
    assert len(packets) == rounds + 1
    assert packets[-1]["kind"] == "final_data_readout"
    patch_maps = [{
        "patch_id": index, "tile_row": row, "tile_col": col,
        "qubit_index_offset": index * stride,
        "measurement_indices": measurement_maps[index],
        "detector_indices": detector_maps[index],
        "observable_indices": list(range(index * base.num_observables,
                                         (index + 1) * base.num_observables)),
    } for index, (row, col) in enumerate(patches)]
    return circuit, packets, patch_maps


def validate_case(folder, circuit, dem, shots, decode_shots):
    """Check every packed record, paired conversion, and a decoding smoke sample."""
    widths = {"measurements": circuit.num_measurements,
              "detections": circuit.num_detectors, "observables": circuit.num_observables}
    data = {}
    for name, width in widths.items():
        path = folder / f"{name}.b8"
        stride = (width + 7) // 8
        assert path.stat().st_size == shots * stride, path
        packed = np.fromfile(path, dtype=np.uint8).reshape(shots, stride)
        if width % 8:
            assert not np.any(packed[:, -1] >> (width % 8)), path
        data[name] = packed
    dets, obs = circuit.compile_m2d_converter().convert(
        measurements=data["measurements"], bit_packed=True,
        bit_pack_result=True, separate_observables=True,
    )
    assert np.array_equal(dets, data["detections"])
    assert np.array_equal(obs, data["observables"])
    clean_dets, clean_obs = circuit.without_noise().compile_detector_sampler(seed=0).sample(
        shots=32, separate_observables=True,
    )
    assert not np.any(clean_dets) and not np.any(clean_obs)
    matching = pymatching.Matching.from_detector_error_model(dem)
    n = min(shots, decode_shots)
    predicted = matching.decode_batch(dets[:n], bit_packed_shots=True, bit_packed_predictions=True)
    failures = int(np.count_nonzero(np.any(predicted != obs[:n], axis=1)))
    return {"all_file_sizes_and_padding_valid": True,
            "all_shots_measurement_to_detector_conversion_matches": True,
            "noiseless_detectors_and_observables_zero": True,
            "pymatching_smoke_shots": n, "pymatching_smoke_shot_failures": failures,
            "logical_failures_are_expected_under_noise": True}


def write_case(folder, shape, distance, rounds, basis, p, shots, seed, decode_shots):
    folder.mkdir(parents=True)
    noise = dict.fromkeys(NOISE_KEYS, p)
    base = stim.Circuit.generated(f"surface_code:rotated_memory_{basis}",
                                  distance=distance, rounds=rounds, **noise)
    circuit, packets, patch_maps = compose_patches(base, shape["occupied_patches"], distance, rounds)
    dem = circuit.detector_error_model(decompose_errors=True)
    (folder / "circuit.stim").write_text(str(circuit) + "\n")
    (folder / "decoder.dem").write_text(str(dem) + "\n")
    circuit.compile_sampler(seed=seed).sample_write(
        shots=shots, filepath=str(folder / "measurements.b8"), format="b8")
    circuit.compile_m2d_converter().convert_file(
        measurements_filepath=str(folder / "measurements.b8"), measurements_format="b8",
        detection_events_filepath=str(folder / "detections.b8"), detection_events_format="b8",
        obs_out_filepath=str(folder / "observables.b8"), obs_out_format="b8")
    active_qubits = sum(i.name == "QUBIT_COORDS" for i in circuit)
    widths = {"measurements": circuit.num_measurements,
              "detections": circuit.num_detectors, "observables": circuit.num_observables}
    payloads = ["circuit.stim", "decoder.dem", "measurements.b8", "detections.b8", "observables.b8"]
    metadata = {
        "schema_version": 1, "circuit_family": "independent_rotated_surface_code_memory",
        "coupled_lattice_surgery": False, "interior_window": False,
        "scope": "Independent patches with fresh initialization and final data readout; no merge/split or temporal-boundary messages",
        "spatial_shape": shape, "distance": distance, "rounds": rounds,
        "basis": basis, "shots": shots, "seed": seed, "noise": noise,
        "stim_version": stim.__version__, "num_active_physical_qubits": active_qubits,
        "qubit_index_upper_bound": circuit.num_qubits,
        "logical_observables": "One memory observable per patch, in patch_id order",
        "files": {f"{name}.b8": {"format": "b8", "bits_per_shot": width,
                  "bytes_per_shot": (width + 7) // 8} for name, width in widths.items()},
        "packing": "Shot-major; little-endian bits; zero-padded to a byte boundary for each shot",
        "shot_semantics": "Each shot is a new experiment; do not concatenate shots as a continuous history",
        "packet_semantics": "Half-open measurement ranges; release after the indexed syndrome round or final readout; no hardware time assumed",
        "coordinate_semantics": "Circuit display coordinates separate independent modules; nominal SWIPER bounds are tile bounds, not physical dimensions",
        "packets": packets, "patch_maps": patch_maps,
        "detector_coordinates": circuit.get_detector_coordinates(),
        "sha256": {name: sha256(folder / name) for name in payloads},
    }
    (folder / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    validation = validate_case(folder, circuit, dem, shots, decode_shots)
    (folder / "validation.json").write_text(json.dumps(validation, indent=2) + "\n")
    return {"case": folder.name, "shape_id": shape["base_shape_id"],
            "nominal_rows": shape["nominal_data_rows"], "nominal_cols": shape["nominal_data_cols"],
            "patches": shape["occupied_patch_count"], "distance": distance,
            "rounds": rounds, "basis": basis, "p": p, "shots": shots, "seed": seed,
            "active_qubits": active_qubits, **{f"{k}_per_shot": v for k, v in widths.items()},
            "pymatching_smoke_shots": validation["pymatching_smoke_shots"],
            "pymatching_smoke_shot_failures": validation["pymatching_smoke_shot_failures"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--shapes", type=Path, default=ROOT / "window-inventory/d3/base_shapes.json")
    parser.add_argument("--distance", type=int, default=3)
    parser.add_argument("--rounds", type=int, nargs="+", default=[1, 3, 6, 12, 24, 48])
    parser.add_argument("--bases", choices=["x", "z"], nargs="+", default=["x", "z"])
    parser.add_argument("--noise", type=float, nargs="+", default=[0.001, 0.01])
    parser.add_argument("--shots", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=20261021)
    parser.add_argument("--decode-validation-shots", type=int, default=128)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("Output exists; use a new directory to preserve existing samples")
    if args.distance != 3:
        parser.error("This inventory describes distance 3; use --distance 3")
    if (min(args.rounds) < 1 or args.shots < 1 or args.decode_validation_shots < 1
            or not all(0 < p < 0.5 for p in args.noise) or not 0 <= args.seed < 2**64):
        parser.error("Positive rounds/shots, 0 < noise < 0.5, unsigned 64-bit seed required")
    rounds_list = sorted(set(args.rounds))
    bases = sorted(set(args.bases))
    noise_list = sorted(set(args.noise))
    shapes = sorted(json.loads(args.shapes.read_text()), key=lambda s: s["base_shape_id"])
    args.out.mkdir(parents=True)
    (args.out / "base_shapes.json").write_text(json.dumps(shapes, indent=2) + "\n")
    for filename in ["benchmark_window_dataset.py", "generate_window_dataset.py"]:
        shutil.copyfile(ROOT / filename, args.out / filename)
    readme_source = ROOT / "window_dataset_readme.md"
    if not readme_source.exists():
        readme_source = ROOT / "README.md"  # Generator copied into a standalone bundle.
    shutil.copyfile(readme_source, args.out / "README.md")
    (args.out / "requirements.txt").write_text(f"stim=={stim.__version__}\npymatching=={pymatching.__version__}\nnumpy=={np.__version__}\n")
    config = {"schema_version": 1, "complete": False,
              "family": "independent_rotated_surface_code_memory",
              "coupled_lattice_surgery": False, "distance": args.distance,
              "rounds": rounds_list, "bases": bases, "noise_probabilities": noise_list,
              "noise_mechanisms": list(NOISE_KEYS), "shots_per_case": args.shots,
              "root_seed": args.seed, "spatial_masks": len(shapes),
              "expected_cases": len(shapes) * len(rounds_list) * len(bases) * len(noise_list),
              "source_inventory_sha256": sha256(args.shapes),
              "stim_version": stim.__version__, "pymatching_version": pymatching.__version__}
    config_path = args.out / "dataset.json"
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    rows = []
    start = time.perf_counter()
    for shape in shapes:
        for rounds in rounds_list:
            for basis in bases:
                for p in noise_list:
                    case = f"{shape['nominal_data_rows']}x{shape['nominal_data_cols']}_{shape['base_shape_id'][:8]}_r{rounds:03d}_{basis}_p{p:g}"
                    folder = args.out / "cases" / case
                    seed = int.from_bytes(hashlib.sha256(f"{args.seed}:{case}".encode()).digest()[:8], "little")
                    row = write_case(folder, shape, args.distance, rounds, basis, p,
                                     args.shots, seed, args.decode_validation_shots)
                    row["case"] = folder.relative_to(args.out).as_posix()
                    rows.append(row)
                    print(f"{len(rows)}/{config['expected_cases']} {case}", flush=True)
    with (args.out / "manifest.csv").open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    config.update(complete=True, completed_cases=len(rows), total_shots=len(rows)*args.shots,
                  generation_and_validation_seconds=time.perf_counter()-start)
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    files = sorted(p for p in args.out.rglob("*") if p.is_file())
    (args.out / "checksums.sha256").write_text("".join(
        f"{sha256(path)}  {path.relative_to(args.out).as_posix()}\n" for path in files))
    print(json.dumps(config, indent=2), flush=True)


if __name__ == "__main__":
    main()
