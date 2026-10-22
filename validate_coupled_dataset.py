"""Independently audit the coupled bundle without importing TQEC."""

import argparse
import csv
import hashlib
import itertools
import json
from pathlib import Path

import stim


def unroll(circuit):
    for instruction in circuit:
        if isinstance(instruction, stim.CircuitRepeatBlock):
            for _ in range(instruction.repeat_count):
                yield from unroll(instruction.body_copy())
        else:
            yield instruction


def audit_case(folder, row):
    meta = json.loads((folder / "metadata.json").read_text())
    validation = json.loads((folder / "validation.json").read_text())
    for name, expected in meta["sha256"].items():
        assert hashlib.sha256((folder/name).read_bytes()).hexdigest() == expected, (folder, name)
    for filename, info in meta["files"].items():
        assert (folder/filename).stat().st_size == meta["shots"]*info["bytes_per_shot"]
    circuit = stim.Circuit.from_file(folder / "circuit.stim")
    assert circuit.num_detectors == int(row["detections_per_shot"])
    assert circuit.num_measurements == int(row["measurements_per_shot"])
    assert circuit.num_observables == int(row["observables_per_shot"])
    assert meta["circuit_graphlike_distance"] == validation["shortest_graphlike_logical_error"] == 3
    assert validation["pymatching_decoded_shots"] == meta["shots"] == int(row["shots"])
    assert validation["all_shots_measurement_to_detector_conversion_matches"]
    assert validation["noiseless_detectors_and_observables_zero"]
    # Verify adjacency, complete footprint coverage, and that only occupied
    # neighboring tiles participate in the surgery schedule.
    occupied = set(map(tuple, meta["spatial_shape"]["occupied_patches"]))
    edges = [p["merge_edge"] for p in meta["phases"] if p["merge_edge"] is not None]
    assert len(edges) == len(occupied)-1
    reached = {min(occupied)}
    while True:
        before = len(reached)
        for first, second in edges:
            first, second = tuple(first), tuple(second)
            assert first in occupied and second in occupied
            assert abs(first[0]-second[0])+abs(first[1]-second[1]) == 1
            if first in reached or second in reached:
                reached.update((first, second))
        if len(reached) == before:
            break
    assert reached == occupied
    phases = meta["phases"]
    assert phases[0]["round_range"][0] == 0
    assert all(a["round_range"][1] == b["round_range"][0] for a, b in zip(phases, phases[1:]))
    assert phases[-1]["round_range"][1] == meta["rounds"] == int(row["rounds"])
    coordinates = circuit.get_final_qubit_coordinates()
    # A real shared bridge-data qubit must interact with ancillas on both
    # sides of its seam during the same merge phase.
    witnesses = {}
    packet_id = measured = detector = segment_id = 0
    measurement_packets = []
    released = {d: p["packet_index"] for p in meta["packets"] for d in p["detector_indices"]}
    assert set(released) == set(range(circuit.num_detectors))
    for instruction in unroll(circuit):
        if instruction.num_measurements:
            measurement_packets.extend([packet_id]*instruction.num_measurements)
            measured += instruction.num_measurements
        elif instruction.name == "DETECTOR":
            dependencies = [measured+t.value for t in instruction.targets_copy()]
            assert released[detector] == max(measurement_packets[m] for m in dependencies) == packet_id
            detector += 1
        elif instruction.name == "SHIFT_COORDS":
            assert instruction.gate_args_copy() == [0, 0, 1]
            packet = meta["packets"][packet_id]
            assert packet["measurement_range"][1] == measured
            assert packet["measurement_range"][0] == (0 if packet_id == 0 else meta["packets"][packet_id-1]["measurement_range"][1])
            packet_id += 1
        elif instruction.name in {"CX", "CZ", "CY"}:
            while packet_id >= phases[segment_id]["round_range"][1]:
                segment_id += 1
            edge = phases[segment_id]["merge_edge"]
            if edge is None:
                continue
            (row1, col1), (row2, col2) = edge
            axis = 0 if row1 == row2 else 1
            seam = 8*(min(col1, col2) if axis == 0 else min(row1, row2))+7
            other = 8*(row1 if axis == 0 else col1)
            targets = instruction.targets_copy()
            for first, second in zip(targets[::2], targets[1::2]):
                for bridge, neighbor in [(first, second), (second, first)]:
                    xy = coordinates[bridge.value]
                    if xy[axis] == seam and xy[1-axis] in (other+1, other+3, other+5):
                        side = -1 if coordinates[neighbor.value][axis] < seam else 1 if coordinates[neighbor.value][axis] > seam else 0
                        witnesses.setdefault(segment_id, {}).setdefault(bridge.value, set()).add(side)
    assert packet_id == meta["rounds"] and measured == circuit.num_measurements
    for index, phase in enumerate(phases):
        if phase["merge_edge"] is not None:
            assert any({-1, 1} <= sides for sides in witnesses.get(index, {}).values()), (folder, index)
    return len(edges)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, nargs="?", default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    root = args.dataset
    config = json.loads((root / "dataset.json").read_text())
    assert config["complete"]
    shapes = json.loads((root / "base_shapes.json").read_text())
    with (root / "manifest.csv").open() as file:
        rows = list(csv.DictReader(file))
    expected = set(itertools.product([s["base_shape_id"] for s in shapes], config["bulk_rounds_per_phase"]))
    actual = {(r["shape_id"], int(r["bulk_rounds_per_phase"])) for r in rows}
    assert len(rows) == len(actual) == config["completed_cases"] == config["expected_cases"]
    assert actual == expected
    assert all(r["basis"] == "z" and float(r["p"]) == config["p"] for r in rows)
    merges = 0
    for index, row in enumerate(rows):
        merges += audit_case(root / row["case"], row)
        print(f"Validated {index+1}/{len(rows)}", flush=True)
    total_shots = sum(int(r["shots"]) for r in rows)
    assert total_shots == config["total_shots"]
    print(json.dumps({"cases": len(rows), "spatial_masks": len(shapes), "shots": total_shots,
                      "merge_phases_with_both_sides_physically_coupled": merges,
                      "all_source_payload_hashes_and_measurement_packets_valid": True}, indent=2))


if __name__ == "__main__":
    main()
