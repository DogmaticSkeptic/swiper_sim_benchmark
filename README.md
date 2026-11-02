# SWIPER decoder-latency experiments

Generate Stim decoder inputs, measure single-job decode latency, fit a volume
model, and supply that function to SWIPER-SIM for scheduling experiments.

The included distance-3 profile uses **CPU PyMatching measurements on
representative coupled surgery experiments**. It is not a GPU/FPGA calibration
or a reconstruction of SWIPER's exact interior-window boundary protocol.
Target-hardware timings remain an experimental input.

[Saved benchmark figures and source data](reports/concurrency/README.md) include
the aggregate heatmaps, combined per-benchmark PDFs, and their recorded inputs.

## Setup

Run the commands below from a Linux shell with Git and Python 3.10 or 3.11
installed (the reference profile was measured with Python 3.10.18). Use
`python3.10` instead of `python3.11` if appropriate for your machine. See the
Python 3.12 packaging note below if using that version.

```bash
git clone https://github.com/DogmaticSkeptic/swiper_sim_benchmark.git
cd swiper_sim_benchmark
python3.11 -m venv .venv
.venv/bin/python bootstrap_sources.py --only swiper-sim
.venv/bin/python -m pip install -r requirements-sim.txt
.venv/bin/python -m pip install --no-deps -e ./swiper-sim
```

If you already have this repository, start with the virtual-environment command
from its root directory. The supplied latency profile and cached schedules are
sufficient to run the timing benchmarks. No decoder measurements or sample
generation are required for a first run.

`upstream-sources.json` pins SWIPER and TQEC to tested commits. Their checkouts
are ignored by the parent repository. The bootstrap script verifies existing
checkouts and refuses to reset or overwrite local changes. SWIPER's large
artifact archive is omitted through sparse checkout.

The `--no-deps` installation deliberately omits SWIPER's separate benchmark
compiler/chemistry packages. Existing compiled schedules work without them.
Consequently `pip check` can report those upstream compiler dependencies as
missing; this is expected for this simulator-only setup. To also generate new
Stim datasets, bootstrap TQEC and install the generation dependencies:

```bash
.venv/bin/python bootstrap_sources.py --only tqec-source
.venv/bin/python -m pip install -r requirements-generation.txt
```

## First benchmark run

From the repository root, run a small seven-simulation sweep of the ten-T-gate
demo, including its baseline:

```bash
.venv/bin/python sweep_concurrency.py \
  --out results/first-run --algorithms t_gate_demo --policies aligned \
  --profile decoder-latency-kit/profiles/cpu-pymatching-d3.json \
  --distance 3 --round-us 1 --counts 1 4 --speeds 1 2 \
  --alphas 0 0.05 --workers 2 --seed 0
.venv/bin/python plot_concurrency.py results/first-run
.venv/bin/python plot_aggregate_concurrency.py results/first-run
```

Open `results/first-run/README.md` for links to the individual plots, or
`results/first-run/average_benchmarks.png` for the aggregate plot. The exact
settings are saved in `configuration.json` and per-run results in `sweep.csv`.
Add `toffoli` to `--algorithms` to include the cached Toffoli schedule; omit
`--algorithms` to run all 45 supplied benchmarks. The larger sweeps and
distributed commands appear under [contention and heatmaps](#shared-accelerator-contention-and-heatmaps).

Repeat the identical sweep command to reuse recorded cells, including recorded
cutoffs and failures. Use a new output directory for different settings or
changed simulation source code. The `results/` directory is ignored by Git;
new local outputs remain there. A selected reference snapshot is included under
[`reports/concurrency/`](reports/concurrency/README.md) in a fresh clone.

## Volume-to-latency model and simulation inputs

The **volume-to-latency model** predicts how long one isolated decoding job
takes. SWIPER supplies the window volume V automatically: the sum of durations
of its commit and buffer regions, in **patch-rounds**. For example, three QEC
rounds on two patches have V = 6. Do not multiply V by d² or manually supply a
volume for every scheduled job.

| Input | How to supply it | Meaning and choice |
|---|---|---|
| Benchmark schedule | `--algorithms t_gate_demo toffoli` | Logical program to simulate; omit to select all supplied schedules. |
| Reference latency model | `--profile path/to/profile.json` | Maps V to isolated latency in microseconds. Use the supplied CPU profile initially, or a profile fitted from your decoder measurements. |
| Code distance d | `--distance 3` | Must match the timing profile. The supplied profile supports distance 3. |
| QEC round duration τ | `--round-us 1` | Physical duration of a syndrome round in microseconds; set it for the intended QPU. |
| Isolated speed multiplier s | `--speeds 0.5 1 2` | Scales reference timing: s = 1 uses the profile, s = 2 halves latency, s = 0.5 doubles latency. |
| Slowdown coefficient α | `--alphas 0 0.05 0.1` | Dimensionless extra latency per additional active decoding job sharing the resource; defined below. |
| Decoder capacity M | `--counts 1 4 8` | Maximum simultaneous decoding jobs sharing one resource. Actual active jobs k can range from 0 to M. |
| Window policy | `--policies aligned parallel sliding` | Selects how windows are constructed and their dependencies. Speculation is a separate simulator setting. |
| Simulation workers | `--workers 4` | Number of independent simulations running on the host at once. This is separate from M inside each simulation. |

The supplied profile is a fitted curve from CPU PyMatching measurements on
representative complete surgery circuits. Calling the function evaluates that
curve; it does not measure the current machine's decoder or decode syndromes.
The curve interpolates between fitted volumes, clamps below the measured
range, and scales proportionally above it. The current measured volume range
is 3–4,606 patch-rounds. Geometry and boundary details are reduced to V, so
these timings are a scheduling surrogate.

### Use the volume-to-latency function

```python
from swiper_latency import load_latency_fn

latency_fn = load_latency_fn(
    "decoder-latency-kit/profiles/cpu-pymatching-d3.json",
    distance=3,
    round_us=1.0,  # Replace with the intended QPU syndrome-round duration.
    statistic="median",
)
volume = 6  # Example only; the simulator supplies actual window volumes.
print(latency_fn.model.latency_us(volume))  # Unrounded microseconds.
print(latency_fn(volume))                   # Integer QEC rounds, rounded up.
```

`latency_fn(V)` returns at least one integer QEC round and can be passed directly
as `decoding_latency_fn` for ordinary integer-latency simulations. The contention
sweep instead calls `latency_fn.model.latency_us(V)` to preserve fractional
timing while applying s. The loader itself requires only the standard library.

### How s, α, and M combine

The reference model T_ref(V) contains **isolated decoding latency only**.
Neither s nor α is fitted automatically when running a sweep. For each new
decode attempt, the sweep initializes remaining work in QEC-round units as:

```text
isolated_latency_us(V) = T_ref(V) / s
initial_work(V) = T_ref(V) / (s * round_us)
```

**α (alpha) is the slowdown coefficient for concurrent decoding jobs.** At the
start of every simulation round with k active decodes, each receives:

```text
slowdown(k) = 1 + alpha * (k - 1)             # k >= 1
work_completed_per_job_per_round = 1 / slowdown(k)
```

Thus α = 0 means no contention slowdown. α = 0.05 adds 5% of the isolated
latency for each additional active job: k = 8 gives a factor of 1.35, or 35%
longer latency if that concurrency stays constant. With only one active job,
α has no effect. M is the capacity limit, while the actual load k determines
the slowdown. Already-running jobs change their progress rate when k changes;
their base work is not resampled. Completion is observed at round boundaries.

For example, if T_ref(V) = 100 µs, s = 2 gives 50 µs isolated latency. At
constant k = 8 and α = 0.05, that becomes 67.5 µs before round-boundary
quantization. The volume function itself still returns the original reference
latency; the simulator applies the contention separately.

Use **s = 1** when the selected profile measures the intended decoder and
hardware. Other s values explore uniform speed changes relative to that
profile. To calibrate α, measure comparable jobs running at steady concurrency
k > 1 and fit `T_k / T_isolated ≈ 1 + alpha * (k - 1)`. A single measurement
gives `alpha ≈ (T_k / T_isolated - 1) / (k - 1)`; use several concurrency levels
to assess whether this linear model describes the hardware. Without those
measurements, α values are exploratory assumptions. Increasing M models more
jobs sharing one accelerator, rather than adding independent accelerators.

The CLI sweep evaluates two slices: selected s values at α = 0, and selected
α values at s = 1. For one specific combination with both s != 1 and α != 0,
use the [Python example below](#run-one-specific-s-alpha-and-m-combination).

### Measure your own decoder

To replace the reference timing, follow [Generate and benchmark coupled samples](#generate-and-benchmark-coupled-samples)
below: measure your decoder, fit a JSON profile with `fit_decoder_latency.py`,
then pass that file using `--profile results/target-profile.json`. Use s = 1
with the new profile and calibrate α separately from concurrent-job timings.
The sweep currently uses the profile's **median** fit. It takes no measurements
on the target hardware during the scheduling simulation.

### Separate speculation demo

The following older demo compares speculation off and on with an integer
latency callback. Use `sweep_concurrency.py` for the s/α/M contention experiments:

```bash
.venv/bin/python run_swiper_demo.py \
  --latency-profile decoder-latency-kit/profiles/cpu-pymatching-d3.json \
  --round-us 1 --decoders 4
```

Add `--schedule swiper-sim/benchmarks/cached_schedules/toffoli.lss` to use a
compiled benchmark. Select `--method sliding`, `parallel`, or `aligned`.

[The latency kit guide](decoder-latency-kit/README.md) explains the custom-decoder
interface, timing scope, fitting, extrapolation, and profile replacement.
The kit includes small reviewed reference timings and a profile, so it can be
used without regenerating the full datasets. Its Python copies are exports;
edit the root scripts and synchronize them with `package_latency_kit.py`.

## Generate and benchmark coupled samples

The requested coupled sample database is **included in Git** at
[`decoder-benchmark-data/d3-coupled-surgery/`](decoder-benchmark-data/d3-coupled-surgery/README.md).
It already contains all 114 cases and 114,000 paired shots. Your collaborator
can benchmark it immediately after cloning and installing the decoder dependencies;
sample generation and TQEC installation are not needed for that workflow.

```bash
python3.10 -m venv .venv
.venv/bin/python -m pip install -r decoder-benchmark-data/d3-coupled-surgery/requirements-decode.txt
.venv/bin/python benchmark_decoder_latency.py \
  decoder-benchmark-data/d3-coupled-surgery --out results/cpu.csv --backend pymatching
```

For their own decoder, use `--backend my_decoder_adapter:build` as described in
the latency kit guide. Earlier independent-memory outputs remain local and are
excluded from Git. To regenerate the coupled corpus into a new directory:

```bash
.venv/bin/python generate_coupled_window_dataset.py \
  --out decoder-benchmark-data/new-coupled-surgery
```

The default is 114 cases: 19 footprints × six bulk-round settings, with Z logical
input/output boundaries, p=0.001, and 1,000 shots per case. The settings
1/3/6/12/24/48 are bulk repetitions per surgery phase; actual total rounds also
include boundary and Hadamard transition layers. Each case contains a circuit,
DEM, paired raw/detector/observable samples, schedule metadata, and validation.
The generator refuses to overwrite an existing directory.

```bash
.venv/bin/python benchmark_decoder_latency.py \
  decoder-benchmark-data/d3-coupled-surgery --out results/target.csv \
  --shots 1000 --backend my_decoder_adapter:build
.venv/bin/python fit_decoder_latency.py \
  --timings results/target.csv \
  --manifest decoder-benchmark-data/d3-coupled-surgery/manifest.csv \
  --out results/target-profile.json
```

Use `--backend pymatching` for the CPU reference. Point `load_latency_fn` at the
resulting JSON. See [the surgery dataset guide](coupled_dataset_readme.md) for
physical scope and round semantics. [Independent-memory generation](window_dataset_readme.md)
and `generate_syndrome_stream.py` provide separate controls.

## Spatial inventory

[Distance-3 summary](window-inventory/d3/README.md) and
[footprint diagram](window-inventory/d3/base_shapes.png) cover 44 supplied
schedule files and three window policies. Compact summaries and the 19 spatial
masks are versioned. Detailed per-run traces and large geometry exports are not.

```bash
.venv/bin/python inventory_windows.py
.venv/bin/python validate_window_inventory.py --require-complete
.venv/bin/python plot_base_shapes.py window-inventory/d3
```

Those commands regenerate detailed outputs; a full inventory run is substantial.
The list describes the reference configuration, rather than all possible pool,
latency, and speculation settings.

## Tests and packaging

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q swiper-sim/tests
.venv/bin/python package_latency_kit.py
```

Tests use small committed fixtures and the pinned upstream schedules, without
requiring the full generated sample corpus. Root pytest discovery excludes the
external source trees and generated outputs. Packaging synchronizes the exported
scripts, rebuilds checksums, and writes an ignored portable archive.

The Git source set includes scripts, tests, small fixtures, documentation,
pinned source specifications, compact inventory summaries, the reference
latency kit, and the complete coupled sample database. Virtual environments,
caches, upstream clones, independent-memory datasets, probe runs, logs, archives,
local profiles, and machine-specific package snapshots are ignored. No Git
repository or remote is created by the setup scripts.

[Detailed reference notes](docs/reference-notes.md) preserve the earlier simulator,
fidelity, timing, and inventory discussion. Paths in those archived notes are
relative to the workspace root. SWIPER models scheduling and stalls; it does not
compute operation fidelity or execute physical correction feedback.

## Shared-accelerator contention and heatmaps

The tracked patch in `patches/swiper-concurrency-slowdown.patch` extends the
pinned SWIPER simulator with `parallel_slowdown_alpha=0.0`. Bootstrap applies it
once, recognizes the exact patched tree on subsequent runs, and refuses partial
patches or unrelated local edits. The patch also corrects detailed trace
recording for unconditional S blocks whose cached group label is
`CONDITIONAL_S`; it leaves their instructions and dependencies intact.

Each decoding attempt samples its isolated work once. At the start of every
QEC round, the decoder manager counts the actual active decodes, k, and advances
all of them by `1 / (1 + alpha * (k - 1))` isolated work rounds. Work remains
fractional until completion at a QEC-round boundary. Jobs already running change
speed as occupancy changes. Capacity M limits concurrent requests sharing one
accelerator; it does not represent M independent accelerators. The manager
exports `DecoderData.concurrency_rounds`, a histogram sampled before progress
and completions, including rounds with k = 0. Existing upstream occupancy fields
sample after completions and are retained for compatibility.

### Run one specific s, alpha, and M combination

For a single simulation, use the Python API. This example runs the ten-T-gate
demo at s = 2, α = 0.05, and M = 8. Save it as a Python script in the repository
root and run it with `.venv/bin/python your_script.py`:

```python
from datetime import timedelta

from swiper.schedule_experiments import RegularTSchedule
from swiper_latency import load_latency_fn
from sweep_runtime import SweepSimulator

round_us = 1.0
distance = 3
s = 2.0
alpha = 0.05
M = 8
latency = load_latency_fn(
    "decoder-latency-kit/profiles/cpu-pymatching-d3.json",
    distance=distance, round_us=round_us, statistic="median",
)

def isolated_work(volume):
    return latency.model.latency_us(volume) / (s * round_us)

success, params, device, windows, decoder = SweepSimulator().run(
    schedule=RegularTSchedule(10, idle_between_Ts=0).schedule,
    distance=distance,
    scheduling_method="aligned",
    decoding_latency_fn=isolated_work,
    max_parallel_processes=M,
    parallel_slowdown_alpha=alpha,
    speculation_mode=None,
    rng=0,
    lightweight_setting=0,
    clock_timeout=timedelta(seconds=900),
    device_rounds_cutoff=5_000_000,
    pending_window_count_cutoff=10_000,
)
if not success:
    raise RuntimeError("Simulation stopped at a cutoff; no completed result")
print("Device QEC rounds:", device.num_rounds)
print("Device duration in microseconds:", device.num_rounds * round_us)
print("Decoding completion round:", decoder.num_rounds)
print("Raw idle patch-rounds:", sum(
    sr.is_unwanted_idle for rounds in device.generated_syndrome_data for sr in rounds
))
```

This uses the same scheduling wrapper as the sweep. For a cached schedule,
load it as follows and pass `schedule=schedule` to the run call:

```python
from pathlib import Path
from sweep_runtime import InventorySchedule

schedule = InventorySchedule.from_str(
    Path("swiper-sim/benchmarks/cached_schedules/toffoli.lss").read_text().strip(),
    generate_dag_incrementally=True,
)
```

The sweep already performs that loading when selecting `--algorithms`.
The raw idle count above includes general scheduling waits. The CLI sweep also
runs a policy-specific baseline and subtracts it for the plotted additional
idle count.

### Run and plot a sweep

Alpha must be finite and nonnegative; work and speed must be finite and
positive. Alpha zero preserves the original integer-callback behavior,
including speculation. Nonzero alpha currently requires speculation to be off:
sharing predictor computation needs an explicit resource model. The sweep uses
zero communication overhead. A fixed transport delay must be modeled separately
from accelerator compute work. This model has no batch formation, padding, or
synchronized batch cohorts.

Run both heatmaps for **all 44 cached schedule files and the ten-T-gate demo**,
under the aligned, parallel, and sliding policies:

```bash
PYTHONHASHSEED=0 .venv/bin/python sweep_concurrency.py \
  --out results/concurrency --workers 32 --round-us 1 --seed 0
.venv/bin/python plot_concurrency.py results/concurrency
.venv/bin/python plot_aggregate_concurrency.py results/concurrency
```

The default experiment uses distance 3, speculation off, CPU PyMatching median
reference timing, and a configurable QPU round duration (1 µs here). Speed uses
unrounded microseconds from `model.latency_us`, divided by speed and round time.
Heatmap 1 scans M = 1, 2, 4, 8, 16, 32 and s = 0.25, 0.5, 1, 2, 4, 8 at alpha
zero. Heatmap 2 scans the same M and alpha = 0, 0.01, 0.025, 0.05, 0.1, 0.2, 0.5
at s = 1. The shared alpha-zero/speed-one cells are simulated once and appear in
both plots. One baseline per algorithm and policy uses unlimited decoder slots,
alpha zero, and one-round latency. There are 9,855 unique simulations, including
135 baselines.

Choose the sweep grid on the command line. For example:

```bash
.venv/bin/python sweep_concurrency.py \
  --out results/custom-grid --algorithms t_gate_demo toffoli \
  --policies aligned parallel --counts 1 3 6 12 \
  --speeds 0.5 1 3 --alphas 0 0.03 0.15 --workers 4
.venv/bin/python plot_concurrency.py results/custom-grid
.venv/bin/python plot_aggregate_concurrency.py results/custom-grid
```

`--counts` sets decoder capacity M, `--speeds` sets isolated speed s, and
`--alphas` sets concurrency slowdown α. The sweep runs two slices: all selected
speeds at α = 0, and all selected α values at s = 1. Include s = 1 and α = 0
in the respective lists so both plots share the same reference row. Counts
must be positive integers, speeds positive, and α nonnegative. Values in each
list must be unique and finite. The lists are saved in `configuration.json`
and used by both plotting commands, including when distributed workers load
the frozen configuration. Older saved sweeps use the original default grid.
`--workers` controls how many simulations run at once; each simulation uses its
own M decoder slots. Changing a frozen configuration requires a new output
directory. Speculation remains disabled for these sweeps.

`--algorithms t_gate_demo toffoli` runs the initial small examples;
`--policies aligned` selects a window policy. Use a separate output directory
for a different configuration. Repeating the same command resumes saved cells.
Every simulation starts in a fresh process with both `PYTHONHASHSEED` and the
simulator RNG seed fixed. The sweep reuses the inventory's tested incremental
DAG construction, conservative future-start prediction bounds, and spatial
boundary filter through `sweep_runtime.py`. With speculation off, simultaneous
completions are independent, so the sweep skips sorting their waiting descendants.
Trace formatting uses a set for conditional-S membership tests, preserving the
formatted trace while avoiding a scan of every S gate for every syndrome object. It runs with `lightweight_setting=0`
and retains the generated syndrome trace for counting unwanted idle patch-rounds.

The raw idle count is the sum of `is_unwanted_idle` over every generated syndrome
round object. Both plots subtract the same policy-specific baseline and label
the result **additional idle patch-rounds relative to the one-round baseline**.
This counts patches, not elapsed QEC rounds. Signed differences are retained.
The profile measures complete representative surgery circuits on a CPU; these
plots are a scheduling surrogate, not calibrated GPU/FPGA performance or exact
interior-window decoding.

Default cutoffs are 900 seconds of simulation wall time, 5,000,000 device
rounds, 10,000 pending windows, and 10 GiB of virtual memory per cell. A parent
watchdog allows another 60 seconds for process startup and result collection.
Cutoffs are frozen in `configuration.json` and recorded in the CSV. The sweep
counts live unfinished windows from the window DAG for its backlog cutoff.
Upstream counts the length of a list that retains merged-away windows, which
can otherwise stop even an unlimited-slot baseline prematurely. Slow cells
can develop growing decoding backlogs as idle rounds create further work.
Cutoff or failed cells are missing values, never zero-idle results. A failed
baseline makes that entire algorithm/policy comparison unavailable.

The output directory contains atomic per-cell JSON, `sweep.csv`, the frozen
configuration, `summary.json`, and an acceptance report checking the common
rows and the M = 1 invariance. CSV records include raw/baseline/additional idle
counts, device and decoding completion rounds, concurrency diagnostics, seeds,
cutoffs, schedule/profile hashes, pinned simulator commit, and patch/driver
hashes. `figures/` contains one heatmap pair per algorithm and policy as PNG,
editable SVG, and PDF, plus `all_benchmarks.pdf` and a comparison atlas. Pairs
share their color scale; the atlas uses one scale across all algorithms and
policies. Zero remains visible under symmetric-log normalization. Figure
captions and an index are written to the output README. Results remain ignored
by Git, and the committed Stim dataset is not regenerated. The aggregate plotting
command writes `average_benchmarks.png`, `.svg`, and `.pdf` in the output
directory. Each cell averages successful benchmark results for that policy and
parameter combination and displays how many benchmarks contributed; cutoff and
failed runs are omitted from the mean.

For example, `34/45 valid` means that 34 benchmarks finished successfully and
had a successful baseline for that cell; the mean uses those 34 results.
Contributors may differ between cells, so sparse cells do not represent the
entire suite. A cutoff means that a simulation stopped before completion, rather
than providing a complete idle count. A backlog cutoff can indicate decoding
falling behind, but does not establish that it would never catch up.

| Plot status | Meaning | Sweep option and default |
|---|---|---|
| T | Simulation time limit or parent watchdog | `--timeout 900` seconds; watchdog allows another 60 seconds |
| B | Too many pending decoding windows | `--backlog-cutoff 10000` |
| R | Too many device QEC rounds | `--round-cutoff 5000000` |
| MEM | Memory limit | `--memory-gib 10` per simulation |
| FAIL / ERR | Process failure or simulation exception | Inspect cell JSON and logs |
| BASE | Baseline unavailable | No additional idle count can be computed for that benchmark/policy |

For a multi-node Slurm run, initialize the frozen output directory first, then use one task per node. Each rank owns disjoint cell IDs:

```bash
.venv/bin/python sweep_concurrency.py --out results/concurrency --initialize-only
srun --jobid=ALLOCATION -N4 -n4 --ntasks-per-node=1 -c128 --hint=nomultithread \
  .venv/bin/python distributed_sweep.py \
  --out results/concurrency --shards 4 --workers 64
.venv/bin/python sweep_concurrency.py --out results/concurrency --aggregate
.venv/bin/python plot_concurrency.py results/concurrency
```

The distributed runner uses a 320 GiB estimated memory budget per node. Its
per-algorithm estimate is twice the observed peak RSS plus allocator headroom,
with a file-size proxy before measurements exist. Increase `--workers` to use
more CPU cores while the memory budget limits overlapping large cases. The
queue wait is outside each cell's timeout. The runner verifies source hashes
and saves node, allocation,
Python, and installed-package metadata under `execution/`. Do not run multiple
controllers for the same rank at once. Validation uses the standard project and
upstream pytest commands above. `test_concurrency.py` compares against the
original pinned decoder manager as well as testing fractional progress, changing
load, saturation, resets, invalid inputs, and full-trace scheduling equivalence.
The upstream package's Python metadata specifies `<=3.12`, which excludes patch
versions of Python 3.12 despite the source running with them. This run used
Python 3.12.12 with `pip install --no-deps --ignore-requires-python -e ./swiper-sim`.

On Perlmutter, attach the runner directly to the interactive allocation to keep
an idle login shell's auto-logout from releasing a running sweep:

```bash
salloc -A m5350 -C cpu -q interactive -N4 -t 04:00:00 \
  srun -N4 -n4 --ntasks-per-node=1 -c128 --hint=nomultithread \
  .venv/bin/python distributed_sweep.py \
  --out results/concurrency --shards 4 --workers 64
```

For several allocations, use the same total `--shards` and disjoint
`--rank-offset` values. Four four-node allocations use `--shards 16` with offsets
0, 4, 8 and 12. Each Slurm task adds its local rank to that offset.
