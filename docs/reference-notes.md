# SWIPER-SIM workspace

Upstream source: https://github.com/jviszlai/swiper, commit
`1c00e097cb7f6d6e82bd88dfb30659f8163e0e33`.

The simulator is in `swiper-sim/`; the isolated Python 3.10 environment is
`.venv/`. Run from this workspace:

```bash
source .venv/bin/activate
python run_swiper_demo.py
python run_swiper_demo.py --round-us 1 --decode-us 20 --decoders 8
cd swiper-sim
python -m pytest -q
```

The upstream walkthrough is `swiper-sim/doc/getting_started.ipynb`. Select
`.venv/bin/python` as its notebook interpreter. The tested demo output is saved
in `demo-results.json`: with 10 T gates, distance 7, 4 decoder slots, and fixed
14-round decoding, device execution took 483 rounds without speculation and
388 with integrated speculation (seed 0). These are illustrative timing results,
not fidelity measurements. All 17 upstream tests passed in this environment.
Stim 1.16.0 and PyMatching 2.4.0 were also verified by sampling and decoding
128 noisy distance-3 memory shots. The installed versions are recorded in
`installed-requirements.txt`.

## Load a measured latency function

The ready-to-use callback and portable calibration tools are in
[`decoder-latency-kit/`](../decoder-latency-kit/README.md). The included profile is
a **CPU PyMatching surrogate fitted to representative surgery circuits**, not
an exact SWIPER interior-window or GPU/FPGA calibration. Your collaborator can
run the kit's single-job benchmark with their own decoder adapter and replace
the profile JSON with their fitted timings.

```python
from swiper_latency import load_latency_fn

latency_fn = load_latency_fn(
    "decoder-latency-kit/profiles/cpu-pymatching-d3.json",
    distance=3, round_us=1.0, statistic="median",
)
# DecodingSimulator().run(..., decoding_latency_fn=latency_fn)
```

To run the existing demo with the profile:

```bash
.venv/bin/python run_swiper_demo.py \
  --latency-profile decoder-latency-kit/profiles/cpu-pymatching-d3.json \
  --round-us 1 --decoders 4
```

The callback accepts patch-round volume and returns integer QEC rounds. Set
`round_us` to the intended device's syndrome-round duration. It supports
median, mean, p95, and p99 curves. The kit includes benchmark/fitting commands,
a custom-decoder interface, extrapolation coverage, and tested demo outputs.

To recreate this subset of the upstream environment with Python 3.10:

```bash
python3.10 -m venv .venv
.venv/bin/python -m pip install -r requirements-sim.txt
.venv/bin/python -m pip install --no-deps -e ./swiper-sim
```

`--no-deps` deliberately omits the benchmark compiler packages declared mandatory
in upstream's metadata. Consequently, `pip check` will report those omitted
packages; the simulator and its upstream test suite run independently of them.

The demo runs ten T gates, comparing aligned window scheduling with and without
integrated speculation. It uses a fixed per-window decoding latency, rounded up
to whole syndrome-extraction rounds. The latency can include communication and
other per-window costs. Shared interconnect contention, batching, and GPU
concurrency effects require additional modeling; increasing the decoder count
assumes independent concurrent decoder slots.

This is a simulation/predictor environment, with NumPy, Matplotlib, NetworkX,
Stim, PyMatching, and notebook/test dependencies. Upstream's separate benchmark
generation/chemistry toolchain (Qualtran, pyLIQTR, tweedledum, PySCF,
OpenFermion-PySCF, Qiskit) is not installed. Built-in schedules and existing
compiled `.lss` schedules do not need it. The source checkout is shallow and
sparse: it includes the simulator, tests, documentation, notebooks, and cached benchmark
schedules, omitting the large artifact result archive. Additional tracked paths
can be fetched with `git sparse-checkout add PATH` inside `swiper-sim/`.

## Generate syndrome inputs for your own decoder

The requested **coupled** distance-3 dataset is in
[`decoder-benchmark-data/d3-coupled-surgery/`](../decoder-benchmark-data/d3-coupled-surgery/README.md).
It contains 114 physical TQEC merge/split experiments: all 19 spatial masks,
six bulk-repetition settings, Z input/output boundaries, p=0.001, and 1,000
shots per case. Its README distinguishes bulk repetitions from total circuit
rounds and explains the representative surgery schedule. These are complete
coupled experiments, not a reconstruction of SWIPER's artificial-boundary
window protocol.

```bash
.venv/bin/python generate_coupled_window_dataset.py \
  --out decoder-benchmark-data/new-coupled-bundle
```

TQEC is installed from the source commit recorded in the bundle. Generation
requires `requirements-generate.txt` from the bundle; loading and benchmarking
need only its `requirements-decode.txt`.

The separate independent-memory baseline is in
[`decoder-benchmark-data/d3-memory-baseline/`](../decoder-benchmark-data/d3-memory-baseline/README.md).
It covers all 19 observed spatial masks with 1/3/6/12/24/48 rounds,
X and Z memory bases, noise 0.001 and 0.01, and 10,000 shots per case.
Each tile is an **independent memory patch**: these are throughput baselines,
not coupled lattice-surgery or interior-window circuits. The dataset README
explains the scope and includes loading, benchmarking, and checksum commands.

To generate another bundle (choose a new output directory):

```bash
.venv/bin/python generate_window_dataset.py --out decoder-benchmark-data/new-bundle
```

`generate_syndrome_stream.py` uses the installed Stim package to generate noisy
rotated surface-code memory experiments. This is a separate data generator;
SWIPER's program scheduler is not generating the measurement bits.

```bash
source .venv/bin/activate
python generate_syndrome_stream.py --out syndrome-data/my-benchmark \
  --distance 7 --rounds 14 --shots 10000 --p 0.001 --basis z
```

For a longer history, increase `--rounds`, for example `--rounds 1000 --shots 100`.
Rounds within one shot preserve their shared error history. Each shot is a fresh
experiment with initialization and final data readout; concatenating shots does
not make one continuous history. `--basis x` changes the memory experiment's
initialization and readout basis. The probability `--p` is applied separately to
post-Clifford depolarization, start-of-round data depolarization, measurement
flips, and reset flips. It is not an aggregate physical-error probability or an
exact reproduction of SWIPER's predictor noise settings.

Each new output directory contains:

| File | Purpose |
| --- | --- |
| `measurements.b8` | Raw ancilla measurement outcomes per round, followed by final data measurements |
| `detections.b8` | Detection-event bits derived from those same raw measurements |
| `observables.b8` | Corresponding logical-observable flips, for scoring decoder predictions |
| `circuit.stim` | Full noisy circuit with detector and observable definitions |
| `decoder.dem` | Decomposed detector error model for graph construction |
| `metadata.json` | Bit counts, noise parameters, measured-qubit order, detector coordinates, packet boundaries, and nominal release times |

The format is Stim `b8`: shot-major packed bits, little-endian within a byte,
with each shot padded to a whole number of bytes. The metadata records each
file's bits and bytes per shot. For example, load detector inputs in Python:

```python
from pathlib import Path
import stim

folder = Path("syndrome-data/d7-r14-p001")  # Already generated example
circuit = stim.Circuit.from_file(folder / "circuit.stim")
events = stim.read_shot_data_file(
    path=folder / "detections.b8", format="b8",
    num_detectors=circuit.num_detectors,
)
model = stim.DetectorErrorModel.from_file(folder / "decoder.dem")
# Build your decoder using model, then benchmark decoding events[i].
# events has shape (shots, detector_bits_per_shot), with Boolean elements.
```

Use `measurements.b8` if your pipeline includes measurement-to-detector
preprocessing; use `detections.b8` if your decoder consumes detection events.
Pre-generate and load the inputs and build the decoder graph before timing
decoding. Measure communication and preprocessing separately or include them
explicitly in an end-to-end latency measurement. A GPU timer must wait for the
requested decoding work to complete to avoid measuring only kernel submission.

The packet layout maps each detector to the earliest packet containing all its
measurement dependencies. `--round-us` defaults to 1; `--readout-us` defaults to
1 and adds a separate final readout interval. These are nominal per-shot replay
times, not simulated hardware timing. The exporter creates files offline; your
hardware replay loop must implement pacing, packet headers, and transport.

These datasets represent finite memory experiments with preparation/readout
boundaries. They do not contain lattice surgery, T gates, or SWIPER's actual
buffered-window boundary messages. Slicing a long detector vector requires the
matching window graph and boundary protocol; an interior window is not
equivalent to a fresh memory experiment. To measure a window decoder's latency,
replay a long shot through that decoder's windowing implementation.

Validation covered noisy and noiseless X/Z memory experiments, raw-to-detector
consistency, bit-packed file sizes, and packet coverage. The saved example at
`syndrome-data/d7-r14-p001/` has 1,000 shots, each with 721 raw measurement bits,
672 detector bits, and one logical-observable bit; all shots decoded successfully
through PyMatching's API (this does not assert zero logical failures).

## What the simulator measures

The program-level `DecodingSimulator` models syndrome-round arrivals, decoding
windows and their dependencies, a finite decoder pool, speculative boundary
predictions and redecoding, and waits for blocking operations. Its success flag
means the scheduling simulation finished before a cutoff; it does not mean the
quantum computation succeeded physically.

* `swiper-sim/swiper/device_manager.py:12`: `SyndromeRound` stores patch, round,
  instruction, and lifecycle metadata, without measurement outcomes/detector bits.
* `swiper-sim/swiper/decoder_manager.py:422`: decoding starts a countdown using
  `decoding_time_function(task.window.total_spacetime_volume())`.
* `swiper-sim/swiper/decoder_manager.py:162`: speculation outcomes are sampled
  from configured probabilities, with face-count and prior-failure modifiers.
  The input `speculation_accuracy` is not a logical success probability.
* `swiper-sim/swiper/device_manager.py:279`: unresolved decoding dependencies
  block conditioned instructions. Idle active patches continue generating
  syndrome-round metadata, including during these waits.
* `swiper-sim/swiper/predictor.py:110`: separate Stim/PyMatching experiments
  assess temporal boundary speculation on noisy surface-code memory samples.
  They are not called by the program-level scheduling loop and do not turn its
  program trace into a circuit-level fidelity calculation.

Consequently, SWIPER-SIM answers the question about how much waiting is required
for a given syndrome production rate and decoding latency, accounting for
dependencies, window buffering, resource contention, and speculation. A
generation rate and latency alone are insufficient: the logical program,
distance/window policy, and pool size also matter.

`device.num_rounds` measures quantum-device execution; `decoder.num_rounds`
also includes any remaining decoding after device execution ends.
`device.total_volume` counts generated patch-rounds, not individual detector bits.
The demo's unwanted-idle patch-round count includes active patches waiting for
any scheduling reason and must not be read as a count of elapsed stall rounds.

## Fidelity and the TRIAGE comparison

SWIPER-SIM does **not** calculate operation fidelity, logical error probability,
or a noisy quantum state in its program-level run. A completed decode is treated
as a verified scheduling result without sampling whether the physical correction
has a logical error. A failed speculation causes recovery work and delays;
it is not automatically an uncorrectable logical failure.

TRIAGE does split generated syndrome history in time and can process later
nonadjacent slices before earlier unresolved slices, subject to temporal/spatial
constraints. It cannot decode unmeasured future syndrome data. Knowing the future
logical instruction schedule is different from knowing future detector outcomes.

TRIAGE's paper additionally estimates overall logical error rate by aggregating
per-layer logical error rates calibrated with circuit-level noisy windowed
lattice-surgery simulations. Section IV describes Stim Monte Carlo at distance
9 and physical error probability 0.003, extrapolated to distance 21. That is an
extra error-modeling layer, not something supplied by installing SWIPER-SIM.
The availability of TRIAGE source code is not established by this installation.

For an initial fidelity estimate, calibrate a logical failure probability `p_L`
per active logical patch per QEC round with a compatible noise model and decoder.
Under an independent-failure approximation, `N` extra idle patch-rounds give

```text
P(at least one added logical-failure event) ≈ 1 - (1 - p_L)^N ≈ N p_L
```

The linear approximation requires `N p_L << 1`. This estimates failure-event
exposure, not quantum process fidelity or an exact final-output error probability.
It ignores correlated faults and error cancellation; surgery operations need
their own rates. An end-to-end accuracy experiment must generate noisy circuits
with the actual inserted QEC cycles, decode their detector samples with the
matching window/boundary protocol, and compare predictions to logical
observables. Delayed classical decoding alone does not decohere a qubit: it is
the extra physical execution and any decoder/protocol errors that matter.

For hardware latency integration, the stock callback sees only volume. It can
sample `latency | volume`; using a particular window identity, arrival timestamp,
or detector count requires an adapter/change at task dispatch. As stalls create
more QEC rounds, a fixed prerecorded arrival trace cannot simply be replayed
unchanged when a new scheduling policy changes the program's timing.

Sources:

* [SWIPER paper, Sections 4.4.2 and 5.1](https://www.jason-chadwick.com/assets/papers/viszlai_SWIPER_2025.pdf)
* [TRIAGE paper, Sections III and IV](https://arxiv.org/pdf/2605.04459)
* [Upstream implementation](https://github.com/jviszlai/swiper/tree/1c00e097cb7f6d6e82bd88dfb30659f8163e0e33/swiper)

## Spatial base sizes across the supplied benchmark schedules

`inventory_windows.py` observes the actual completed decoding windows emitted by
SWIPER's window managers. It does not change the upstream scheduler. It covers
all 44 cached schedule files (24 top-level traces and 20 random-T variants),
including one duplicate source trace, under sliding, parallel, and aligned
window scheduling. The reference configuration is distance 3, constant one-round
decoding, unlimited decoder slots, no speculation, and random seed 0.

```bash
source .venv/bin/activate
MPLBACKEND=Agg python inventory_windows.py
```

The script checkpoints completed runs in `window-inventory/d3/runs/` and resumes
them when rerun. Its default three workers process smaller schedules in parallel;
large schedules run one at a time to limit memory consumption. To inventory a
different latency or decoder pool, choose a new output directory:

```bash
python inventory_windows.py --out window-inventory/d3-latency14-pool8 \
  --distance 3 --decode-rounds 14 --decoders 8
```

The spatial outputs ignore the number of rounds, as requested:

* `base_sizes.csv`: distinct bounding dimensions and occupied-patch counts,
  aggregated over all benchmark runs.
* `base_sizes_by_benchmark.csv`: the same information separated by benchmark
  and window scheduling policy.
* `base_shapes.json`: exact occupied-patch coordinates for each distinct spatial
  footprint, normalized by translation; rotations are kept distinct. Each mask
  is the spatial projection of all commit and buffer regions of a window.
* `commit_base_sizes.csv` and `commit_base_shapes.json`: the corresponding core
  footprints, excluding buffers.

At distance 3 every basic patch is modeled as 3 by 3 data-grid units. For example,
a footprint two patches high and three patches wide has a nominal 6 by 9 bounding
box. This is a dimension proxy, not a count of physical data-plus-ancilla qubits
or a complete physical lattice-surgery circuit. Nonrectangular shapes can occupy
less area than their bounding boxes; consult the patch masks rather than
replacing every footprint with a filled rectangle. A spatial projection can
also combine patches active at different times, so the region-level outputs
retain that timing information for later circuit construction.

The additional window-size and region-shape files retain temporal information
for reproducibility. Occurrence counts count constructed windows once; they are
not a count of redecoding attempts. The list is complete for the recorded runs,
not an exhaustive guarantee over all seeds, decoder latencies, resource limits,
and speculation outcomes. Those can alter stalls and window geometry.
Occurrence counts can also vary with upstream's unordered-set tie breaking;
the saved per-run records are the source of truth for this inventory.

The inventory wrapper uses upstream's incremental dependency-graph builder,
preserving the default parser's edge ordering and deep-copy behavior. It skips
far-future start-time calculations using conservative earliest-start bounds and
retires finalized verified windows only after all neighboring tasks are also
verified, with speculation disabled. These changes keep large traces within
memory limits. Spatially unrelated windows are rejected using cached patch
connectivity before applying the upstream exact boundary comparison.
`test_inventory_windows.py` compares complete geometry histograms
and runtimes to the original scheduler across four programs and all three
policies (12 comparisons). The upstream source checkout is unchanged.

After generating the inventory, render its spatial masks with:

```bash
python plot_base_shapes.py window-inventory/d3
```

Verify source hashes, instruction counts, geometry histograms, exports, and full
reference-run coverage with:

```bash
python validate_window_inventory.py --require-complete
```
