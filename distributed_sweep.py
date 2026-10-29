"""Shard a frozen concurrency sweep across Slurm tasks without duplicate cells.

Initialize configuration with sweep_concurrency.py first. Run one Slurm task
per node. All ranks share the same output directory and frozen model settings.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import time
import threading

from sweep_concurrency import ROOT, tasks, task_id, run_one, atomic_json, digest


class MemoryBudget:
    """Limit estimated concurrent memory; queues do not consume cell timeout."""
    def __init__(self, capacity):
        self.capacity = capacity
        self.used = 0.
        self.condition = threading.Condition()

    def acquire(self, amount):
        amount = min(self.capacity, amount)
        with self.condition:
            self.condition.wait_for(lambda: self.used + amount <= self.capacity)
            self.used += amount
        return amount

    def release(self, amount):
        with self.condition:
            self.used -= amount
            self.condition.notify_all()


def memory_estimates(out, config):
    peaks = {}
    for path in (out/'cells').glob('*.json'):
        record = json.loads(path.read_text())
        algorithm = record['algorithm']
        peaks[algorithm] = max(peaks.get(algorithm, 0.), record.get('peak_rss_gib', 0.))
    # At least twice observed peak RSS, plus interpreter/allocator headroom.
    # Before measurements exist, use input file size as a conservative proxy.
    return {t['algorithm']: max(.5, 2*peaks.get(t['algorithm'],
                (Path(t['schedule']).stat().st_size/1024**2*.6+1) if t['schedule'] else .1)+.5)
            for t in tasks(config)}


def budgeted_run(config, task, out, budget, estimates):
    if (out/'cells'/f'{task_id(task)}.json').exists():
        return run_one(config, task, out)
    amount = budget.acquire(estimates[task['algorithm']])
    try:
        return run_one(config, task, out)
    finally:
        budget.release(amount)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--shards',type=int,required=True)
    p.add_argument('--workers',type=int,default=48)
    p.add_argument('--memory-budget-gib',type=float,default=320.)
    p.add_argument('--rank',type=int,default=int(os.environ.get('SLURM_PROCID','0')))
    p.add_argument('--rank-offset',type=int,default=0)
    args=p.parse_args();out=args.out.resolve()
    args.rank += args.rank_offset
    if not 0<=args.rank<args.shards or args.workers<1 or not 0<args.memory_budget_gib< float('inf'):p.error('Invalid rank, workers, or memory budget')
    config=json.loads((out/'configuration.json').read_text())
    for name,field in [('sweep_concurrency.py','driver_sha256'),('sweep_runtime.py','runtime_sha256'),
                       ('inventory_windows.py','inventory_sha256'),('patches/swiper-concurrency-slowdown.patch','patch_sha256')]:
        if digest(ROOT/name)!=config[field]:raise RuntimeError(f'Source changed since configuration was frozen: {name}')
    estimates=memory_estimates(out,config)
    budget=MemoryBudget(args.memory_budget_gib)
    metadata=dict(memory_budget_gib=args.memory_budget_gib,estimated_peak_rss_gib=estimates,rank=args.rank,shards=args.shards,workers=args.workers,host=socket.gethostname(),
                  job_id=os.environ.get('SLURM_JOB_ID'),python=sys.version,platform=platform.platform(),
                  packages=subprocess.check_output([sys.executable,'-m','pip','freeze'],text=True).splitlines())
    atomic_json(out/'execution'/f'{socket.gethostname()}__{args.rank}.json',metadata)
    selected=[t for t in tasks(config) if int(task_id(t),16)%args.shards==args.rank]
    selected.sort(key=lambda t:(not t['baseline'],
                               Path(t['schedule']).stat().st_size if t['schedule'] else 0,
                               t['algorithm'],t['policy']))
    print(f'Rank {args.rank}: {len(selected)} cells, {args.workers} workers',flush=True)
    start=time.monotonic()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures={pool.submit(budgeted_run,config,t,out,budget,estimates):t for t in selected}
        for n,f in enumerate(as_completed(futures),1):
            status,cached=f.result()
            if n%25==0 or status not in ['ok','backlog_cutoff']:
                print(f'rank={args.rank} {n}/{len(selected)} {status} cached={cached} elapsed={time.monotonic()-start:.0f}s {futures[f]["algorithm"]}',flush=True)
    print(f'Rank {args.rank} complete',flush=True)


if __name__=='__main__':main()
