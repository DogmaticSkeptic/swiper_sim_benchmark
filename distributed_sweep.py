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

from sweep_concurrency import ROOT, tasks, task_id, run_one, atomic_json, digest


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--shards',type=int,required=True)
    p.add_argument('--workers',type=int,default=48)
    p.add_argument('--rank',type=int,default=int(os.environ.get('SLURM_PROCID','0')))
    args=p.parse_args();out=args.out.resolve()
    if not 0<=args.rank<args.shards or args.workers<1:p.error('Invalid rank or workers')
    config=json.loads((out/'configuration.json').read_text())
    for name,field in [('sweep_concurrency.py','driver_sha256'),('sweep_runtime.py','runtime_sha256'),
                       ('inventory_windows.py','inventory_sha256'),('patches/swiper-concurrency-slowdown.patch','patch_sha256')]:
        if digest(ROOT/name)!=config[field]:raise RuntimeError(f'Source changed since configuration was frozen: {name}')
    metadata=dict(rank=args.rank,shards=args.shards,workers=args.workers,host=socket.gethostname(),
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
        futures={pool.submit(run_one,config,t,out):t for t in selected}
        for n,f in enumerate(as_completed(futures),1):
            status,cached=f.result()
            if n%25==0 or status not in ['ok','backlog_cutoff']:
                print(f'rank={args.rank} {n}/{len(selected)} {status} cached={cached} elapsed={time.monotonic()-start:.0f}s {futures[f]["algorithm"]}',flush=True)
    print(f'Rank {args.rank} complete',flush=True)


if __name__=='__main__':main()
