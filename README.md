# SWIPER decoder-latency experiments

Generate Stim decoder inputs, measure single-job decode latency, fit a volume
model, and supply that function to SWIPER-SIM for scheduling experiments.

The included distance-3 profile uses **CPU PyMatching measurements on
representative coupled surgery experiments**. It is not a GPU/FPGA calibration
or a reconstruction of SWIPER's exact interior-window boundary protocol.
Target-hardware timings remain an experimental input.

## Setup

Use Python 3.10–3.12 (tested with 3.10.18). Fetch the pinned upstream sources
before installing packages:

```bash
python3 bootstrap_sources.py
python3.10 -m venv .venv
.venv/bin/python -m pip install -r requirements-generation.txt
.venv/bin/python -m pip install --no-deps -e ./swiper-sim
```

`upstream-sources.json` pins SWIPER and TQEC to tested commits. Their checkouts
are ignored by the parent repository. The bootstrap script verifies existing
checkouts and refuses to reset or overwrite local changes. SWIPER's large
artifact archive is omitted through sparse checkout.

The `--no-deps` installation deliberately omits SWIPER's separate benchmark
compiler/chemistry packages. Existing compiled schedules work without them.
Consequently `pip check` can report those upstream compiler dependencies as
missing; this is expected for this simulator-only setup. Source generation uses
TQEC, installed through `requirements-generation.txt`.

## Use the latency function

```python
from swiper_latency import load_latency_fn

latency_fn = load_latency_fn(
    "decoder-latency-kit/profiles/cpu-pymatching-d3.json",
    distance=3,
    round_us=1.0,  # Replace with the intended QPU syndrome-round duration.
    statistic="median",
)
# DecodingSimulator().run(..., decoding_latency_fn=latency_fn)
```

The input is total commit-plus-buffer duration in patch-rounds. Do not multiply
it by d². The output is a positive integer number of QEC rounds, rounded upward.
The loader itself requires only the standard library.

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

```python
latency = load_latency_fn(
    "decoder-latency-kit/profiles/cpu-pymatching-d3.json",
    distance=3, round_us=1.0,
)
speed = 2.0

def isolated_work(volume):
    return latency.model.latency_us(volume) / (speed * latency.model.round_us)

# DecodingSimulator().run(...,
#     decoding_latency_fn=isolated_work,
#     parallel_slowdown_alpha=0.05,
#     max_parallel_processes=8, speculation_mode=None)
```

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

`--algorithms t_gate_demo toffoli` runs the initial small examples;
`--policies aligned` selects a window policy. Use a separate output directory
for a different configuration. Repeating the same command resumes saved cells.
Every simulation starts in a fresh process with both `PYTHONHASHSEED` and the
simulator RNG seed fixed. The sweep reuses the inventory's tested incremental
DAG construction, conservative future-start prediction bounds, and spatial
boundary filter through `sweep_runtime.py`. It runs with `lightweight_setting=0`
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
Cutoffs are frozen in `configuration.json` and recorded in the CSV. Slow cells
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
by Git, and the committed Stim dataset is not regenerated.

For a multi-node Slurm run, initialize the frozen output directory first, then use one task per node. Each rank owns disjoint cell IDs:

```bash
.venv/bin/python sweep_concurrency.py --out results/concurrency --initialize-only
srun --jobid=ALLOCATION -N4 -n4 --ntasks-per-node=1 -c48 \
  .venv/bin/python distributed_sweep.py \
  --out results/concurrency --shards 4 --workers 48
.venv/bin/python sweep_concurrency.py --out results/concurrency --aggregate
.venv/bin/python plot_concurrency.py results/concurrency
```

The distributed runner verifies source hashes and saves node, allocation,
Python, and installed-package metadata under `execution/`. Do not run multiple
controllers for the same rank at once. Validation uses the standard project and
upstream pytest commands above. `test_concurrency.py` compares against the
original pinned decoder manager as well as testing fractional progress, changing
load, saturation, resets, invalid inputs, and full-trace scheduling equivalence.
The upstream package's Python metadata specifies `<=3.12`, which excludes patch
versions of Python 3.12 despite the source running with them. This run used
Python 3.12.12 with `pip install --no-deps --ignore-requires-python -e ./swiper-sim`.
