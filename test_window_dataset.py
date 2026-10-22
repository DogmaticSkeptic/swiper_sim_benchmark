"""Check physical independence, rec remapping, and the exported file contract."""

from collections import Counter

import numpy as np
import pytest
import stim

from generate_window_dataset import compose_patches, NOISE_KEYS, write_case


def error_terms(model, detector_map=None, observable_map=None):
    terms = Counter()
    for instruction in model.flattened():
        if instruction.type != "error":
            continue
        targets = []
        for target in instruction.targets_copy():
            if target.is_relative_detector_id():
                targets.append(("d", target.val if detector_map is None else detector_map[target.val]))
            elif target.is_logical_observable_id():
                targets.append(("l", target.val if observable_map is None else observable_map[target.val]))
            else:
                raise AssertionError(target)
        key = tuple(sorted(targets))
        p = instruction.args_copy()[0]
        # Distinct error mechanisms with identical symptoms combine by parity.
        terms[key] = terms[key] + p - 2 * terms[key] * p
    return terms


@pytest.mark.parametrize("patches", [
    [(0, 0)], [(0, 0), (0, 1), (1, 0)],
    [(0, 0), (0, 1), (1, 0), (1, 1), (1, 2), (2, 0), (2, 1)],
])
@pytest.mark.parametrize("basis", ["x", "z"])
@pytest.mark.parametrize("rounds", [1, 3, 12])
def test_parallel_composition_matches_independent_error_models(patches, basis, rounds):
    base = stim.Circuit.generated(f"surface_code:rotated_memory_{basis}",
                                  distance=3, rounds=rounds, **dict.fromkeys(NOISE_KEYS, .003))
    circuit, packets, maps = compose_patches(base, patches, 3, rounds)
    assert circuit.num_measurements == len(patches) * base.num_measurements
    assert circuit.num_detectors == len(patches) * base.num_detectors
    assert circuit.num_observables == len(patches)
    assert sum(len(p["measured_qubits"]) for p in packets) == circuit.num_measurements
    assert sorted(d for p in packets for d in p["detector_indices"]) == list(range(circuit.num_detectors))
    assert sorted(m for p in maps for m in p["measurement_indices"]) == list(range(circuit.num_measurements))
    expected = Counter()
    for patch in maps:
        expected.update(error_terms(base.detector_error_model(),
                                    patch["detector_indices"], patch["observable_indices"]))
    actual = error_terms(circuit.detector_error_model())
    assert actual.keys() == expected.keys()
    for term in actual:
        assert actual[term] == pytest.approx(expected[term], abs=1e-14)
    # Independently infer detector release packets from the actual rec targets.
    measurement_packet = [packet["packet_index"] for packet in packets
                          for _ in range(*packet["measurement_range"])]
    releases = {d: p["packet_index"] for p in packets for d in p["detector_indices"]}
    measured = detector = 0
    for instruction in circuit:
        measured += instruction.num_measurements
        if instruction.name == "DETECTOR":
            assert releases[detector] == max(
                measurement_packet[measured + t.value] for t in instruction.targets_copy())
            detector += 1
    dets, obs = circuit.without_noise().compile_detector_sampler(seed=9).sample(
        shots=32, separate_observables=True)
    assert not np.any(dets) and not np.any(obs)


@pytest.mark.parametrize("basis", ["x", "z"])
def test_export_paired_packed_records(tmp_path, basis):
    shape = {"base_shape_id": "example", "occupied_patches": [[0, 0], [0, 1], [1, 0]],
             "nominal_data_rows": 6, "nominal_data_cols": 6, "occupied_patch_count": 3}
    result = write_case(tmp_path / "case", shape, 3, 6, basis, .001, 65, 91, 65)
    assert result["shots"] == 65
    assert result["active_qubits"] == 51
    assert result["observables_per_shot"] == 3
    assert result["pymatching_smoke_shots"] == 65
