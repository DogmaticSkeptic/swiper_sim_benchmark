import json
from pathlib import Path

import pytest
from tqec import NoiseModel, compile_block_graph
from tqec.compile.detectors.database import DetectorDatabase
from tqec.utils.scale import LinearFunction

from generate_coupled_window_dataset import (
    build_surgery_graph, coupling_evidence, measurement_packets, save_and_validate,
)


@pytest.fixture(scope="module")
def detector_db():
    return DetectorDatabase()


@pytest.mark.parametrize("patch_count", [1, 3, 7])
@pytest.mark.parametrize("bulk", [1, 3])
def test_physical_surgery_and_round_contract(tmp_path, detector_db, patch_count, bulk):
    shapes = json.loads(Path("window-inventory/d3/base_shapes.json").read_text())
    shape = next(s for s in shapes if s["occupied_patch_count"] == patch_count)
    graph, phases = build_surgery_graph(shape)
    assert len(phases) == 2*patch_count-1
    assert sum(phase["merge_edge"] is not None for phase in phases) == patch_count-1
    compiled = compile_block_graph(graph, block_temporal_height=LinearFunction(2, bulk-2))
    circuit = compiled.generate_stim_circuit(k=1, noise_model=NoiseModel.uniform_depolarizing(.001),
                                             detector_database=detector_db, database_path=None)
    assert len(circuit.shortest_graphlike_error()) == 3
    packets = measurement_packets(circuit, phases, bulk)
    vertical_merges = sum(p["merge_edge"] is not None and p["merge_edge"][0][1] == p["merge_edge"][1][1] for p in phases)
    assert len(packets) == len(phases)*(bulk+2)+2*vertical_merges
    assert sorted(d for p in packets for d in p["detector_indices"]) == list(range(circuit.num_detectors))
    assert sum(p["measurement_range"][1]-p["measurement_range"][0] for p in packets) == circuit.num_measurements
    evidence = coupling_evidence(circuit, phases, bulk)
    assert all(count > 0 for phase, count in zip(phases, evidence) if phase["merge_edge"] is not None)
    folder = tmp_path / "case"
    folder.mkdir()
    widths, validation = save_and_validate(folder, circuit,
        circuit.detector_error_model(decompose_errors=True), 65, 42)
    assert validation["pymatching_decoded_shots"] == 65
    assert validation["all_shots_measurement_to_detector_conversion_matches"]
    assert widths["observables"] > 0


def test_reject_disconnected_footprint():
    with pytest.raises(ValueError, match="connected"):
        build_surgery_graph({"base_shape_id": "disconnected", "occupied_patches": [[0, 0], [0, 2]]})
