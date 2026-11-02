"""Reproducible, restartable SWIPER contention sweeps on cached schedules.

Every simulation runs in a fresh process with fixed hash/RNG seeds. Results
are atomic JSON records; aggregate CSV and figures are generated separately.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parent
COUNTS = [1, 2, 4, 8, 16, 32]
SPEEDS = [.25, .5, 1, 2, 4, 8]
ALPHAS = [0, .01, .025, .05, .1, .2, .5]
POLICIES = ['aligned', 'parallel', 'sliding']


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def positive(value):
    x = float(value)
    if not math.isfinite(x) or x <= 0:
        raise argparse.ArgumentTypeError('Value must be finite and positive')
    return x


def nonnegative(value):
    x = float(value)
    if not math.isfinite(x) or x < 0:
        raise argparse.ArgumentTypeError('Value must be finite and nonnegative')
    return x


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2, allow_nan=False)+'\n')
    tmp.replace(path)


def schedules():
    base = ROOT/'swiper-sim/benchmarks/cached_schedules'
    return {'t_gate_demo': None, **{str(p.relative_to(base).with_suffix('')): str(p)
                                  for p in sorted(base.rglob('*.lss'))}}


def task_id(task):
    return hashlib.sha256(json.dumps(task, sort_keys=True).encode()).hexdigest()[:20]


def tasks(config):
    counts = config.get('counts', COUNTS)
    speeds = config.get('speeds', SPEEDS)
    alphas = config.get('alphas', ALPHAS)
    result = []
    for algorithm, schedule in schedules().items():
        if config['algorithms'] and algorithm not in config['algorithms']:
            continue
        for policy in config['policies']:
            common = dict(algorithm=algorithm, schedule=schedule, policy=policy)
            result.append(dict(common, baseline=True, M=None, speed=1., alpha=0.))
            cells = [(m, s, 0.) for s in speeds for m in counts]
            cells += [(m, 1., a) for a in alphas if a != 0 for m in counts]
            result.extend(dict(common, baseline=False, M=m, speed=s, alpha=a) for m,s,a in cells)
    return result


def record_metadata(config, task):
    result = dict(task)
    result.pop('schedule')
    result.update(distance=config['distance'], qpu_round_us=config['round_us'],
                  seed=config['seed'], hash_seed=config['hash_seed'], speculation='off',
                  communication_overhead_us=0, profile=config['profile'],
                  profile_sha256=config['profile_sha256'], patch_sha256=config['patch_sha256'],
                  simulator_commit=config['simulator_commit'], config_sha256=config['config_sha256'],
                  driver_sha256=config['driver_sha256'], runtime_sha256=config['runtime_sha256'],
                  schedule_sha256=config['schedule_hashes'][task['algorithm']],
                  device_rounds_cutoff=config['round_cutoff'],
                  pending_window_count_cutoff=config['backlog_cutoff'],
                  wall_seconds_cutoff=config['timeout'], memory_gib_cutoff=config['memory_gib'])
    return result


def simulate(config, task):
    import datetime as dt
    import resource
    # Bound pathological cells as well as time/backlog/rounds. BLAS threads are
    # fixed before startup, so virtual mappings do not scale with node cores.
    if config['memory_gib']:
        limit = int(config['memory_gib'] * 1024**3)
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    from swiper.lattice_surgery_schedule import LatticeSurgerySchedule
    from swiper.schedule_experiments import RegularTSchedule
    from sweep_runtime import SweepSimulator, InventorySchedule
    from swiper_latency import load_latency_fn
    started = time.monotonic()
    result = record_metadata(config, task)
    try:
        schedule = (InventorySchedule.from_str(Path(task['schedule']).read_text().strip(), generate_dag_incrementally=True)
                    if task['schedule'] else RegularTSchedule(10, idle_between_Ts=0).schedule)
        model = load_latency_fn(config['profile'], round_us=config['round_us'],
                                distance=config['distance']).model
        observed = set()
        def isolated_work(volume):
            observed.add(volume)
            work = 1. if task['baseline'] else model.latency_us(volume)/(task['speed']*config['round_us'])
            if not math.isfinite(work) or work <= 0:
                raise ValueError('Isolated work must be finite and positive')
            return work
        sim = SweepSimulator()
        success, params, device, windows, decoder = sim.run(
            schedule=schedule, distance=config['distance'], scheduling_method=task['policy'],
            decoding_latency_fn=isolated_work, max_parallel_processes=task['M'],
            parallel_slowdown_alpha=task['alpha'], speculation_mode=None,
            rng=config['seed'], lightweight_setting=0,
            device_rounds_cutoff=config['round_cutoff'],
            pending_window_count_cutoff=config['backlog_cutoff'],
            clock_timeout=dt.timedelta(seconds=config['timeout']))
        status = 'ok'
        if not success:
            if sim._device_manager.current_round > config['round_cutoff']:
                status = 'round_cutoff'
            elif sim.pending_window_count() > config['backlog_cutoff']:
                status = 'backlog_cutoff'
            else:
                status = 'time_cutoff'
        hist = decoder.concurrency_rounds
        active_rounds = sum(n for k,n in hist.items() if k)
        busy_volume = sum(k*n for k,n in hist.items())
        result.update(status=status, raw_idle_patch_rounds=sum(
            sr.is_unwanted_idle for rounds in device.generated_syndrome_data for sr in rounds),
            device_rounds=device.num_rounds, decoder_completion_rounds=decoder.num_rounds,
            completed_windows=decoder.num_completed_windows,
            peak_pending_windows=sim.peak_pending_windows,
            concurrency_rounds=hist, concurrency_peak=max(hist, default=0),
            concurrency_mean=busy_volume/sum(hist.values()) if hist else 0,
            concurrency_mean_when_busy=busy_volume/active_rounds if active_rounds else 0,
            concurrency_active_rounds=active_rounds, concurrency_work_rounds=busy_volume,
            observed_volume_min=min(observed, default=None), observed_volume_max=max(observed, default=None))
    except Exception as exc:
        result.update(status='memory_cutoff' if isinstance(exc, MemoryError) else 'error',
                      error=f'{type(exc).__name__}: {exc}', traceback=traceback.format_exc())
    result.update(elapsed_seconds=time.monotonic()-started,
                  peak_rss_gib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024**2)
    return result


def run_one(config, task, out):
    key = task_id(task)
    target = out/'cells'/f'{key}.json'
    if target.exists():
        old = json.loads(target.read_text())
        if old.get('config_sha256') != config['config_sha256']:
            raise RuntimeError(f'Configuration mismatch: {target}')
        return old['status'], True
    env = dict(os.environ, PYTHONHASHSEED=str(config['hash_seed']), OMP_NUM_THREADS='1',
               OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1', MPLBACKEND='Agg',
               MPLCONFIGDIR=str(out/'mpl-cache'))
    cmd = [sys.executable, str(Path(__file__).resolve()), '--cell', json.dumps(task),
           '--out', str(out)]
    log = out/'logs'/f'{key}.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('w') as stream:
        process = subprocess.Popen(cmd, env=env, stdout=stream, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        watchdog = False
        try:
            process.wait(timeout=config['timeout']+60)
        except subprocess.TimeoutExpired:
            watchdog = True
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        if not target.exists():
            failed = record_metadata(config,task)
            failed.update(status='watchdog_cutoff' if watchdog else 'process_failed',
                          returncode=process.returncode, log=str(log))
            atomic_json(target, failed)
    return json.loads(target.read_text())['status'], False


def aggregate(out):
    config = json.loads((out/'configuration.json').read_text())
    records = [json.loads(p.read_text()) for p in sorted((out/'cells').glob('*.json'))]
    baselines = {(r['algorithm'],r['policy']): r for r in records if r['baseline']}
    for r in records:
        b = baselines.get((r['algorithm'],r['policy']))
        r['baseline_status'] = b['status'] if b else 'missing'
        r['baseline_idle_patch_rounds'] = b.get('raw_idle_patch_rounds') if b and b['status']=='ok' else None
        r['additional_idle_patch_rounds'] = (r['raw_idle_patch_rounds']-r['baseline_idle_patch_rounds']
            if r['status']=='ok' and r['baseline_idle_patch_rounds'] is not None else None)
    fields = sorted(set().union(*(r.keys() for r in records))) if records else ['status']
    with (out/'sweep.csv').open('w',newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader()
        for r in records:
            writer.writerow({k:json.dumps(v,sort_keys=True) if isinstance(v,(dict,list)) else v for k,v in r.items()})
    from collections import Counter
    summary = dict(expected_cells=len(tasks(config)), recorded_cells=len(records),
                   statuses=dict(Counter(r['status'] for r in records)),
                   comparable_cells=sum(r['additional_idle_patch_rounds'] is not None for r in records))
    atomic_json(out/'summary.json',summary)
    print(json.dumps(summary),flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT/'results/concurrency')
    parser.add_argument('--profile',type=Path,default=ROOT/'decoder-latency-kit/profiles/cpu-pymatching-d3.json')
    parser.add_argument('--round-us',type=positive,default=1.)
    parser.add_argument('--distance',type=int,default=3)
    parser.add_argument('--seed',type=int,default=0)
    parser.add_argument('--workers',type=int,default=32)
    parser.add_argument('--timeout',type=positive,default=900.)
    parser.add_argument('--round-cutoff',type=int,default=5_000_000)
    parser.add_argument('--backlog-cutoff',type=int,default=10_000)
    parser.add_argument('--memory-gib',type=positive,default=10.)
    parser.add_argument('--algorithms',nargs='+')
    parser.add_argument('--policies',nargs='+',choices=POLICIES,default=POLICIES)
    parser.add_argument('--counts', nargs='+', type=int, default=COUNTS,
                        help='Decoder capacities M to sweep (default: %(default)s)')
    parser.add_argument('--speeds', nargs='+', type=positive, default=SPEEDS,
                        help='Isolated decoder speed multipliers s (must include 1)')
    parser.add_argument('--alphas', nargs='+', type=nonnegative, default=ALPHAS,
                        help='Concurrency slowdown coefficients alpha (must include 0)')
    parser.add_argument('--cell',help=argparse.SUPPRESS)
    parser.add_argument('--aggregate',action='store_true')
    parser.add_argument('--initialize-only',action='store_true')
    args = parser.parse_args()
    out = args.out.resolve(); out.mkdir(parents=True,exist_ok=True)
    if args.cell:
        config = json.loads((out/'configuration.json').read_text()); task=json.loads(args.cell)
        atomic_json(out/'cells'/f'{task_id(task)}.json',simulate(config,task)); return
    if args.aggregate:
        aggregate(out); return
    if min(args.distance,args.workers,args.round_cutoff,args.backlog_cutoff)<=0:
        parser.error('Distance, workers, and cutoffs must be positive')
    if any(count <= 0 for count in args.counts) or len(set(args.counts)) != len(args.counts):
        parser.error('Decoder counts must be unique positive integers')
    if len(set(args.speeds)) != len(args.speeds) or 1.0 not in args.speeds:
        parser.error('Speeds must be unique and include 1 for the alpha heatmap')
    if len(set(args.alphas)) != len(args.alphas) or 0.0 not in args.alphas:
        parser.error('Alphas must be unique and include 0 for the speed heatmap')
    if args.algorithms and set(args.algorithms)-set(schedules()):
        parser.error('Unknown algorithm')
    config = dict(distance=args.distance, round_us=args.round_us, seed=args.seed,
        hash_seed=args.seed, driver_sha256=digest(__file__),
        runtime_sha256=digest(ROOT/'sweep_runtime.py'), inventory_sha256=digest(ROOT/'inventory_windows.py'), profile=str(args.profile.resolve()), profile_sha256=digest(args.profile),
        patch_sha256=digest(ROOT/'patches/swiper-concurrency-slowdown.patch'),
        simulator_commit=json.loads((ROOT/'upstream-sources.json').read_text())['swiper-sim']['commit'],
        timeout=args.timeout, round_cutoff=args.round_cutoff, backlog_cutoff=args.backlog_cutoff,
        memory_gib=args.memory_gib, algorithms=args.algorithms, policies=args.policies,
        counts=args.counts, speeds=args.speeds, alphas=args.alphas,
        schedule_hashes={a:digest(p) if p else hashlib.sha256(b'RegularTSchedule(10, idle_between_Ts=0)').hexdigest()
                         for a,p in schedules().items()})
    config['config_sha256']=hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()
    frozen=out/'configuration.json'
    if frozen.exists() and json.loads(frozen.read_text()) != config:
        raise RuntimeError('Output directory has a different frozen configuration; choose a new directory')
    atomic_json(frozen,config)
    if args.initialize_only:
        print(f'Initialized {out}',flush=True); return
    all_tasks=tasks(config)
    # Prioritize baseline and small algorithms, without changing any cell settings.
    all_tasks.sort(key=lambda t:(0 if t['baseline'] else 1,
        Path(t['schedule']).stat().st_size if t['schedule'] else 0, t['algorithm'], t['policy']))
    print(f'Starting {len(all_tasks)} cells with {args.workers} workers',flush=True)
    started=time.monotonic()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures={pool.submit(run_one,config,t,out):t for t in all_tasks}
        for n,f in enumerate(as_completed(futures),1):
            status,cached=f.result()
            if n%25==0 or status!='ok':
                print(f'{n}/{len(all_tasks)} {status} cached={cached} elapsed={time.monotonic()-started:.0f}s {futures[f]["algorithm"]}',flush=True)
    aggregate(out)


if __name__=='__main__':
    main()
