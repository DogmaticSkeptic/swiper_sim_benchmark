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
