"""Generate physical, coupled merge/split experiments for the 19 SWIPER masks.

The experiments represent each footprint, not a reconstruction of the original
SWIPER algorithm or its decoder-specific artificial-boundary protocol.
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
from tqec import BlockGraph, NoiseModel, compile_block_graph
from tqec.compile.detectors.database import DetectorDatabase
from tqec.utils.position import Position3D
from tqec.utils.scale import LinearFunction

ROOT = Path(__file__).resolve().parent
TQEC_COMMIT = "60928c3297bb092baf7d66f7bbb264e6d94a17e9"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_surgery_graph(shape):
    """Use a deterministic spanning tree and one adjacent merge per time slice."""
    patches = sorted(map(tuple, shape["occupied_patches"]))
    seen = {patches[0]}
    queue = [patches[0]]
    edges = []
    for row, col in queue:
        for other in [(row, col+1), (row+1, col), (row, col-1), (row-1, col)]:
            if other in patches and other not in seen:
                seen.add(other)
                queue.append(other)
                edges.append(((row, col), other))
    if len(seen) != len(patches):
        raise ValueError("Footprint must be connected")
    phases = [{"kind": "separate", "merge_edge": None}]
    for edge in edges:
        phases.extend([{"kind": "merge", "merge_edge": edge},
                       {"kind": "split_separate", "merge_edge": None}])
    graph = BlockGraph(f"Z-input-output merge/split {shape['base_shape_id']}")
    for z, phase in enumerate(phases):
        edge = phase["merge_edge"]
        vertical = edge is not None and edge[0][1] == edge[1][1]
        for row, col in patches:
            # Rotating a patch's boundary orientation for a vertical merge is
            # represented by actual temporal Hadamard pipes, compiled by TQEC.
            kind = "XZZ" if vertical and (row, col) in edge else "ZXZ"
            graph.add_cube(Position3D(col, row, z), kind)
    for z in range(len(phases)-1):
        for row, col in patches:
            graph.add_pipe(Position3D(col, row, z), Position3D(col, row, z+1))
    for z, phase in enumerate(phases):
        if phase["merge_edge"] is not None:
            (row, col), (row2, col2) = phase["merge_edge"]
            graph.add_pipe(Position3D(col, row, z), Position3D(col2, row2, z))
    graph.validate()
    return graph, phases


def unroll_preserving_shifts(circuit):
    for instruction in circuit:
        if isinstance(instruction, stim.CircuitRepeatBlock):
            for _ in range(instruction.repeat_count):
                yield from unroll_preserving_shifts(instruction.body_copy())
        else:
            yield instruction


def phase_timeline(phases, bulk_rounds):
    timeline = []
    cursor = 0
    def vertical(phase):
        edge = phase["merge_edge"]
        return edge is not None and edge[0][1] == edge[1][1]
    for index, phase in enumerate(phases):
        timeline.append({"logical_phase_index": index, **phase,
                         "round_range": [cursor, cursor+bulk_rounds+2]})
        cursor += bulk_rounds+2
        if index+1 < len(phases) and (vertical(phase) or vertical(phases[index+1])):
            timeline.append({"logical_phase_index": None, "kind": "temporal_hadamard",
                             "merge_edge": None, "between_phases": [index, index+1],
                             "round_range": [cursor, cursor+1]})
            cursor += 1
    return timeline


def measurement_packets(circuit, phases, bulk_rounds):
    """Use TQEC's round-end coordinate shifts; verify actual detector causality."""
    packets = []
    timeline = phase_timeline(phases, bulk_rounds)
    segment_index = 0
    measured = detector = 0
    measurement_release = []
    packet = {"measurement_range": [0, None], "measured_qubits": [], "detector_indices": []}
    for instruction in unroll_preserving_shifts(circuit):
        count = instruction.num_measurements
        if count:
            if instruction.name not in {"M", "MX", "MY", "MR", "MRX", "MRY"}:
                raise ValueError(f"Unsupported measurement type: {instruction.name}")
            measurement_release.extend([len(packets)] * count)
            packet["measured_qubits"].extend(t.value for t in instruction.targets_copy())
            measured += count
        elif instruction.name == "DETECTOR":
            deps = [measured + t.value for t in instruction.targets_copy()
                    if t.is_measurement_record_target]
            assert deps and min(deps) >= 0
            release = max(measurement_release[d] for d in deps)
            assert release == len(packets), (detector, release, len(packets))
            packet["detector_indices"].append(detector)
            detector += 1
        elif instruction.name == "SHIFT_COORDS":
            assert instruction.gate_args_copy() == [0, 0, 1]
            packet["measurement_range"][1] = measured
            packet["packet_index"] = len(packets)
            while len(packets) >= timeline[segment_index]["round_range"][1]:
                segment_index += 1
            segment = timeline[segment_index]
            packet["schedule_segment_index"] = segment_index
            packet["logical_phase_index"] = segment["logical_phase_index"]
            packet["phase_kind"] = segment["kind"]
            packet["round_in_segment"] = len(packets)-segment["round_range"][0]
            packets.append(packet)
            packet = {"measurement_range": [measured, None], "measured_qubits": [], "detector_indices": []}
    assert measured == circuit.num_measurements and detector == circuit.num_detectors
    assert not packet["measured_qubits"] and not packet["detector_indices"]
    assert len(packets) == timeline[-1]["round_range"][1]
    return packets


def coupling_evidence(circuit, phases, bulk_rounds):
    """Count entangling gates involving bridge-data qubits during each merge."""
    coordinates = circuit.get_final_qubit_coordinates()
    round_index = 0
    timeline = phase_timeline(phases, bulk_rounds)
    segment_index = 0
    counts = [0] * len(phases)
    for instruction in unroll_preserving_shifts(circuit):
        if instruction.name == "SHIFT_COORDS":
            round_index += 1
        elif instruction.name in {"CX", "CZ", "CY"}:
            while round_index >= timeline[segment_index]["round_range"][1]:
                segment_index += 1
            segment = timeline[segment_index]
            phase_index = segment["logical_phase_index"]
            edge = segment["merge_edge"]
            if edge is None:
                continue
            (row, col), (row2, col2) = edge
            # At k=1 the TQEC block pitch is 8 coordinate units. Ordinary
            # patch data are at local coordinates 1,3,5; bridge data at 7.
            bridge_axis = 0 if row == row2 else 1
            bridge_coordinate = 8 * (min(col, col2) if bridge_axis == 0 else min(row, row2)) + 7
            other_base = 8 * (row if bridge_axis == 0 else col)
            targets = instruction.targets_copy()
            for first, second in zip(targets[::2], targets[1::2]):
                for target in (first, second):
                    xy = coordinates[target.value]
                    if (xy[bridge_axis] == bridge_coordinate
                            and xy[1-bridge_axis] in [other_base+1, other_base+3, other_base+5]):
                        counts[phase_index] += 1
    for phase, count in zip(phases, counts):
        assert phase["merge_edge"] is None or count > 0
    return counts


def save_and_validate(folder, circuit, dem, shots, seed):
    circuit.to_file(folder / "circuit.stim")
    dem.to_file(folder / "decoder.dem")
    circuit.compile_sampler(seed=seed).sample_write(
        shots=shots, filepath=str(folder / "measurements.b8"), format="b8")
    circuit.compile_m2d_converter().convert_file(
        measurements_filepath=str(folder / "measurements.b8"), measurements_format="b8",
        detection_events_filepath=str(folder / "detections.b8"), detection_events_format="b8",
        obs_out_filepath=str(folder / "observables.b8"), obs_out_format="b8")
    widths = {"measurements": circuit.num_measurements, "detections": circuit.num_detectors,
              "observables": circuit.num_observables}
    arrays = {}
    for name, width in widths.items():
        path = folder / f"{name}.b8"
        stride = (width + 7) // 8
        assert path.stat().st_size == shots * stride
        arrays[name] = np.fromfile(path, dtype=np.uint8).reshape(shots, stride)
        if width % 8:
            assert not np.any(arrays[name][:, -1] >> (width % 8))
    dets, obs = circuit.compile_m2d_converter().convert(
        measurements=arrays["measurements"], bit_packed=True,
        bit_pack_result=True, separate_observables=True)
    assert np.array_equal(dets, arrays["detections"])
    assert np.array_equal(obs, arrays["observables"])
    clean_dets, clean_obs = circuit.without_noise().compile_detector_sampler(seed=0).sample(
        shots=32, separate_observables=True)
    assert not clean_dets.any() and not clean_obs.any()
    matching = pymatching.Matching.from_detector_error_model(dem)
    predictions = matching.decode_batch(dets, bit_packed_shots=True, bit_packed_predictions=True)
    assert predictions.shape == obs.shape
    failures = int(np.count_nonzero(np.any(predictions != obs, axis=1)))
    validation = {"all_packed_file_sizes_and_padding_valid": True,
                  "all_shots_measurement_to_detector_conversion_matches": True,
                  "noiseless_detectors_and_observables_zero": True,
                  "pymatching_decoded_shots": shots, "pymatching_shot_failures": failures,
                  "logical_failures_expected_under_noise": True}
    return widths, validation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--shapes", type=Path, default=ROOT / "window-inventory/d3/base_shapes.json")
    parser.add_argument("--bulk-rounds", type=int, nargs="+", default=[1, 3, 6, 12, 24, 48])
    parser.add_argument("--shots", type=int, default=1000)
    parser.add_argument("--p", type=float, default=.001)
    parser.add_argument("--seed", type=int, default=20261021)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("Output exists; choose a new directory")
    if min(args.bulk_rounds) < 1 or args.shots < 1 or not 0 < args.p < .5 or not 0 <= args.seed < 2**64:
        parser.error("Positive bulk rounds/shots, 0<p<0.5, unsigned 64-bit seed required")
    shapes = sorted(json.loads(args.shapes.read_text()), key=lambda s: (s["occupied_patch_count"], s["base_shape_id"]))
    bulk_values = sorted(set(args.bulk_rounds))
    args.out.mkdir(parents=True)
    (args.out / "base_shapes.json").write_text(json.dumps(shapes, indent=2) + "\n")
    for filename in ["generate_coupled_window_dataset.py", "benchmark_window_dataset.py", "validate_coupled_dataset.py"]:
        shutil.copyfile(ROOT / filename, args.out / filename)
    readme = ROOT / "coupled_dataset_readme.md"
    shutil.copyfile(readme if readme.exists() else ROOT / "README.md", args.out / "README.md")
    (args.out / "requirements-decode.txt").write_text(
        f"stim=={stim.__version__}\npymatching=={pymatching.__version__}\nnumpy=={np.__version__}\n")
    (args.out / "requirements-generate.txt").write_text(
        f"-r requirements-decode.txt\ntqec @ git+https://github.com/tqec/tqec.git@{TQEC_COMMIT}\n")
    config = {"schema_version": 1, "complete": False, "distance": 3, "scale_k": 1,
              "basis": "Z input/output temporal boundaries", "p": args.p,
              "noise_model": "TQEC NoiseModel.uniform_depolarizing",
              "shots_per_case": args.shots, "bulk_rounds_per_phase": bulk_values,
              "round_semantics": "Total rounds=(2*patch_count-1)*(bulk_rounds+2)+2*vertical_merge_count, including Hadamard transition layers",
              "spatial_masks": len(shapes), "expected_cases": len(shapes)*len(bulk_values),
              "source_inventory_sha256": digest(args.shapes), "tqec_commit": TQEC_COMMIT,
              "stim_version": stim.__version__, "root_seed": args.seed,
              "scope": "Representative full merge/split experiments; not original SWIPER schedules or artificial-boundary windows"}
    config_path = args.out / "dataset.json"
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    db = DetectorDatabase()
    rows = []
    start = time.perf_counter()
    noise = NoiseModel.uniform_depolarizing(args.p)
    for shape in shapes:
        graph, phases = build_surgery_graph(shape)
        for bulk in bulk_values:
            case = f"{shape['nominal_data_rows']}x{shape['nominal_data_cols']}_{shape['base_shape_id'][:8]}_bulk{bulk:03d}_z_p{args.p:g}"
            folder = args.out / "cases" / case
            folder.mkdir(parents=True)
            seed = int.from_bytes(hashlib.sha256(f"{args.seed}:{case}".encode()).digest()[:8], "little")
            # Nonzero slope is required by TQEC's scalable block API; at k=1
            # this evaluates to the requested number of bulk rounds.
            compiled = compile_block_graph(graph, block_temporal_height=LinearFunction(2, bulk-2))
            circuit = compiled.generate_stim_circuit(k=1, noise_model=noise,
                                                     detector_database=db, database_path=None)
            dem = circuit.detector_error_model(decompose_errors=True)
            distance = len(circuit.shortest_graphlike_error())
            assert distance == 3, (case, distance)
            packets = measurement_packets(circuit, phases, bulk)
            bridges = coupling_evidence(circuit, phases, bulk)
            widths, validation = save_and_validate(folder, circuit, dem, args.shots, seed)
            validation.update(shortest_graphlike_logical_error=distance,
                              every_merge_has_bridge_entangling_gates=all(
                                  phase["merge_edge"] is None or count > 0 for phase, count in zip(phases, bridges)),
                              detector_release_packets_verified=True)
            graph.to_json(folder / "block_graph.json")
            phase_records = phase_timeline(phases, bulk)
            for record in phase_records:
                index = record["logical_phase_index"]
                record["bridge_entangling_gate_count"] = 0 if index is None else bridges[index]
            metadata = {"schema_version": 1, "circuit_family": "TQEC sequential adjacent merge/split",
                "coupled_lattice_surgery": len(phases) > 1, "single_patch_memory_control": len(phases) == 1,
                "interior_window": False, "exact_swiper_window": False,
                "spatial_shape": shape, "distance": 3, "circuit_graphlike_distance": distance,
                "basis": "Z input/output boundaries; vertical merges include temporal Hadamard orientation changes",
                "noise": {"model": "TQEC uniform_depolarizing", "p": args.p},
                "shots": args.shots, "seed": seed, "bulk_rounds_per_phase": bulk,
                "rounds": len(packets), "total_measurement_rounds": len(packets),
                "logical_phase_count": len(phases), "schedule_segment_count": len(phase_records), "phases": phase_records,
                "num_active_physical_qubits": len(circuit.get_final_qubit_coordinates()),
                "qubit_index_upper_bound": circuit.num_qubits,
                "files": {f"{name}.b8": {"format": "b8", "bits_per_shot": width,
                          "bytes_per_shot": (width+7)//8} for name, width in widths.items()},
                "packing": "Shot-major, little-endian bits, byte padding per shot",
                "shot_semantics": "Fresh initialized and finally read-out full surgery experiment per shot",
                "packets": packets, "detector_coordinates": circuit.get_detector_coordinates(),
                "observable_semantics": "TQEC auto correlation surfaces for the full computation; not one observable per patch",
                "tqec_commit": TQEC_COMMIT, "stim_version": stim.__version__,
                "sha256": {name: digest(folder/name) for name in ["circuit.stim", "decoder.dem", "measurements.b8",
                            "detections.b8", "observables.b8", "block_graph.json"]}}
            (folder / "metadata.json").write_text(json.dumps(metadata, indent=2)+"\n")
            (folder / "validation.json").write_text(json.dumps(validation, indent=2)+"\n")
            rows.append({"case": folder.relative_to(args.out).as_posix(), "shape_id": shape["base_shape_id"],
                "nominal_rows": shape["nominal_data_rows"], "nominal_cols": shape["nominal_data_cols"],
                "patches": shape["occupied_patch_count"], "distance": 3, "bulk_rounds_per_phase": bulk,
                "rounds": len(packets), "phases": len(phases), "basis": "z", "p": args.p,
                "shots": args.shots, "seed": seed, "coupled": len(phases)>1,
                **{f"{name}_per_shot": width for name, width in widths.items()},
                "pymatching_shot_failures": validation["pymatching_shot_failures"]})
            print(f"{len(rows)}/{config['expected_cases']} {case}, actual rounds={len(packets)}", flush=True)
    with (args.out / "manifest.csv").open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    config.update(complete=True, completed_cases=len(rows), total_shots=len(rows)*args.shots,
                  generation_and_validation_seconds=time.perf_counter()-start)
    config_path.write_text(json.dumps(config, indent=2)+"\n")
    (args.out / "checksums.sha256").write_text("".join(
        f"{digest(p)}  {p.relative_to(args.out).as_posix()}\n"
        for p in sorted(args.out.rglob("*")) if p.is_file()))
    print(json.dumps(config, indent=2), flush=True)


if __name__ == "__main__":
    main()
