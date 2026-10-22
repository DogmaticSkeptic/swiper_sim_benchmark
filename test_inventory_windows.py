"""Verify inventory optimizations preserve upstream scheduling and geometry."""

import contextlib
import io
from pathlib import Path

import pytest

from inventory_windows import InventorySchedule, InventorySimulator
from swiper.lattice_surgery_schedule import LatticeSurgerySchedule


@pytest.mark.parametrize("benchmark", ["msd_15to1", "toffoli", "rz_1e-05", "adder_n18"])
@pytest.mark.parametrize("method", ["sliding", "parallel", "aligned"])
def test_inventory_matches_original(benchmark, method):
    text = (Path(__file__).parent / "swiper-sim" / "benchmarks" /
            "cached_schedules" / f"{benchmark}.lss").read_text().strip()
    schedules = [LatticeSurgerySchedule.from_str(text),
                 InventorySchedule.from_str(text, generate_dag_incrementally=True)]
    for distance in [None, 3]:
        original, optimized = [schedule.to_dag(distance) for schedule in schedules]
        assert dict(original.nodes(data=True)) == dict(optimized.nodes(data=True))
        assert {n: list(original.predecessors(n)) for n in original} == {
            n: list(optimized.predecessors(n)) for n in optimized}
        assert {n: list(original.successors(n)) for n in original} == {
            n: list(optimized.successors(n)) for n in optimized}
    results = []
    for optimized, schedule in enumerate(schedules):
        if optimized:
            schedule.inventory_consumable = True
        simulator = InventorySimulator(3, fast_prediction=bool(optimized), retire_history=bool(optimized))
        with contextlib.redirect_stdout(io.StringIO()):
            success, _, device, _, decoder = simulator.run(
                schedule=schedule, distance=3, scheduling_method=method,
                decoding_latency_fn=lambda _volume: 1, rng=0, lightweight_setting=2,
            )
        assert success
        assert sum(simulator.sizes.values()) == decoder.num_completed_windows
        results.append((device.num_rounds, decoder.num_rounds,
                        simulator.sizes, simulator.shapes))
    assert results[0] == results[1]
