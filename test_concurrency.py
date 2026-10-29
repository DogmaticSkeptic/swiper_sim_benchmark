"""Contention, attempt lifecycle, and exact integer-callback regression checks."""
import dataclasses
import math
from pathlib import Path
import subprocess
import sys
import types

import networkx as nx
import pytest
from swiper.decoder_manager import DecoderManager
from swiper.simulator import DecodingSimulator
from swiper.schedule_experiments import RegularTSchedule


class Window:
    constructed = True
    parent_instr_idx = [-1]

    def __init__(self, idx, volume=1):
        self.window_idx, self.volume = idx, volume

    def total_spacetime_volume(self):
        return self.volume


def manager(work=3, alpha=0, capacity=None):
    return DecoderManager(nx.DiGraph(), lambda v: work, 1, 0,
                          max_parallel_processes=capacity, speculation_mode=None,
                          parallel_slowdown_alpha=alpha, rng=0)


def start(m, indices):
    dag = m._window_idx_dag.copy()
    dag.add_nodes_from(indices)
    m.update_decoding([Window(i) for i in indices], [], dag)


@pytest.mark.parametrize('alpha', [0, .01, .05, .5])
@pytest.mark.parametrize('k', [1, 2, 8])
def test_constant_occupancy(alpha, k):
    m = manager(work=2.3, alpha=alpha)
    start(m, range(k))
    rounds = math.ceil(2.3 * (1 + alpha * (k-1)) - 1e-10)
    for _ in range(rounds-1):
        assert m.step() == []
    assert set(m.step()) == set(range(k))
    assert m.get_data().concurrency_rounds == {k: rounds}


def test_changing_load_affects_existing_work():
    m = manager(work=3, alpha=1)
    start(m, [0])
    m.step()
    assert m._active_window_progress[0] == 2
    start(m, [1])
    m.step()
    assert m._active_window_progress == {0: 1.5, 1: 2.5}
    for _ in range(3):
        m.step()
    assert m._active_window_progress == {1: 1}
    assert m.step() == [1]
    assert m.get_data().concurrency_rounds == {1: 2, 2: 4}


def test_empty_fractional_and_tolerance():
    m = manager(.1, alpha=1)
    m.step()
    start(m, range(10))
    assert set(m.step()) == set(range(10))
    assert m.get_data().concurrency_rounds == {0: 1, 10: 1}


def test_saturation_and_reset_samples_new_attempt():
    calls = []
    m = manager(alpha=.5, capacity=1)
    m.decoding_time_function = lambda v: calls.append(v) or 3.5
    start(m, [0, 1])
    idx = next(iter(m._active_window_progress))
    assert len(calls) == 1 and len(m._pending_decode_tasks) == 1
    m.step()
    m._reset_decode_task(idx)
    assert not m._active_window_progress
    m.update_decoding([], [], m._window_idx_dag)
    assert len(calls) == 2
    assert list(m._active_window_progress.values()) == [3.5]
    for _ in range(4):
        m.step()
    m.update_decoding([], [], m._window_idx_dag)
    assert len(calls) == 3


@pytest.mark.parametrize('alpha', [-1, float('inf'), float('nan')])
def test_invalid_alpha(alpha):
    with pytest.raises(ValueError):
        manager(alpha=alpha)


@pytest.mark.parametrize('work', [0, -1, float('inf'), float('nan'), True])
def test_invalid_work(work):
    with pytest.raises(ValueError):
        start(manager(work), [0])


def test_speculation_resource_sharing_must_be_defined():
    with pytest.raises(ValueError, match='speculation'):
        DecoderManager(nx.DiGraph(), lambda v: 2, 1, 1,
                       parallel_slowdown_alpha=.1)


def upstream_module(name, path):
    module = types.ModuleType(name)
    sys.modules[name] = module
    source = subprocess.check_output(['git', 'show', f'HEAD:{path}'],
                                    cwd=Path(__file__).parent/'swiper-sim', text=True)
    exec(compile(source, path, 'exec'), module.__dict__)
    return module


@pytest.fixture(scope='module')
def original_simulator():
    dm = upstream_module('reference_decoder', 'swiper/decoder_manager.py')
    sim = upstream_module('reference_simulator', 'swiper/simulator.py')
    sim.DecoderManager = dm.DecoderManager
    return sim.DecodingSimulator


@pytest.mark.parametrize('policy', ['aligned', 'parallel', 'sliding'])
@pytest.mark.parametrize('speculation', [None, 'integrated', 'separate'])
def test_alpha_zero_matches_upstream(original_simulator, policy, speculation):
    settings = dict(distance=3, scheduling_method=policy,
                    decoding_latency_fn=lambda v: 5 + v//3,
                    speculation_mode=speculation, speculation_accuracy=.65,
                    speculation_latency=1, max_parallel_processes=4, rng=0)
    a = original_simulator().run(RegularTSchedule(6, idle_between_Ts=0).schedule, **settings)
    b = DecodingSimulator().run(RegularTSchedule(6, idle_between_Ts=0).schedule, **settings)
    assert a[0] and b[0]
    assert a[2].to_dict() == b[2].to_dict()
    old = a[4].to_dict()
    assert old == {key: b[4].to_dict()[key] for key in old}


@pytest.mark.parametrize('policy', ['aligned', 'parallel', 'sliding'])
def test_single_decoder_invariant(policy):
    settings = dict(distance=3, scheduling_method=policy,
                    decoding_latency_fn=lambda v: 1.3 + v/4,
                    max_parallel_processes=1, rng=0)
    a = DecodingSimulator().run(RegularTSchedule(5, idle_between_Ts=0).schedule,
                                parallel_slowdown_alpha=0, **settings)
    b = DecodingSimulator().run(RegularTSchedule(5, idle_between_Ts=0).schedule,
                                parallel_slowdown_alpha=.5, **settings)
    assert a[2].to_dict() == b[2].to_dict()
    assert a[4].window_decoding_completion_times == b[4].window_decoding_completion_times
    assert b[1].parallel_slowdown_alpha == .5


@pytest.mark.parametrize('benchmark', ['toffoli', 'rz_1e-05', 'msd_15to1'])
@pytest.mark.parametrize('policy', ['aligned', 'parallel', 'sliding'])
def test_sweep_optimization_preserves_full_trace(benchmark, policy):
    from sweep_runtime import SweepSimulator, InventorySchedule
    from swiper.lattice_surgery_schedule import LatticeSurgerySchedule
    text=(Path(__file__).parent/'swiper-sim/benchmarks/cached_schedules'/f'{benchmark}.lss').read_text().strip()
    runs=[]
    for cls, schedule in [(DecodingSimulator,LatticeSurgerySchedule.from_str(text)),
                          (SweepSimulator,InventorySchedule.from_str(text,generate_dag_incrementally=True))]:
        success, _, device, windows, decoder=cls().run(schedule=schedule,distance=3,
            scheduling_method=policy,decoding_latency_fn=lambda v: .3+v/100,
            parallel_slowdown_alpha=.2,max_parallel_processes=4,lightweight_setting=0,rng=0,device_rounds_cutoff=5000)
        assert success
        runs.append((device.to_dict(),decoder.to_dict()))
    assert runs[0]==runs[1]


def test_prediction_path_preserves_contention_parameter():
    success, params, _, _, decoder=DecodingSimulator().run(
        schedule=RegularTSchedule(3,idle_between_Ts=0).schedule,distance=3,
        scheduling_method='aligned',decoding_latency_fn=lambda v:3,
        max_parallel_processes='predict',parallel_slowdown_alpha=.1,
        speculation_mode=None,rng=0)
    assert success
    assert params.parallel_slowdown_alpha==decoder.parallel_slowdown_alpha==.1
    assert max(decoder.concurrency_rounds)<=params.max_parallel_processes
