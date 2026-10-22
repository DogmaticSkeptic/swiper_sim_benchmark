# Distance-3 coupled lattice-surgery dataset

This bundle contains physical Stim merge/split circuits and paired samples for
all 19 observed SWIPER spatial masks. Each adjacent merge is implemented by
TQEC stabilizer circuits with **bridge data qubits and entangling gates**. This
is a coupled dataset, not a disjoint union of independent memory circuits.
The one-tile footprint is necessarily a single-patch memory control.

## Cases and rounds

Default settings are **114 cases, 1,000 shots per case, Z input/output boundaries,
and p=0.001**. There are six bulk-repetition settings per mask:
1, 3, 6, 12, 24, and 48. Actual parameters are in `dataset.json` and `manifest.csv`.

**The bulk setting is not the total circuit round count.** A deterministic
breadth-first spanning tree connects each footprint. The experiment first runs
the patches separately, then merges and splits one adjacent pair at a time,
visiting every tree edge. All patches continue error correction during every
phase. For N occupied tiles, there are 2N−1 phases. TQEC adds two boundary layers
to each phase, and each vertical merge needs two additional Hadamard transition
layers. If V tree edges are vertical, the total number of measurement rounds is:

```
total_measurement_rounds = (2*N - 1) * (bulk_rounds_per_phase + 2) + 2*V
```

For example, the horizontal two-tile cases have 9/15/24/42/78/150 total rounds;
the vertical two-tile cases have 11/17/26/44/80/152. The seven-tile cases have
47/73/112/190/346/658 total rounds. Names say `bulk003`, not `r003`,
to avoid confusing the two. The manifest gives actual rounds and detector counts
for fitting latency. Every circuit was checked to have shortest graphlike
logical-error length 3, including the shortest repetition setting.

Horizontal merges use TQEC's ZXZ orientation. For vertical merges the two involved
patches change to XZZ and back; **actual temporal Hadamard pipes** implement the
orientation changes. Initial and final logical boundaries are Z. Intermediate
stabilizer measurements and orientation changes also use X bases. Logical
observables are TQEC's automatically computed full-computation correlation
surfaces; they are not indexed as one simple observable per patch.

## Scope of the benchmark

These are **representative, complete merge/split experiments** with the requested
footprints. They are not a physical recompilation of each original SWIPER
algorithm, and are not exact SWIPER artificial-boundary decoding windows.
SWIPER's footprint projection alone does not identify surgery type, timing,
commit/buffer roles, or the interior window's boundary-message protocol.
The spanning-tree schedule here is explicitly supplied in each `block_graph.json`
and in metadata. It includes preparation, actual merge and split transitions,
context, and final readout. A benchmark using the full `decoder.dem` measures
decoding that full experiment. Use the actual detector count and total rounds.

An interior-window decoder needs to process the full stream through its own
window/boundary implementation. `packets` lists every round's raw measurements
and available detector IDs for that purpose. Arbitrarily cropping detector
vectors and keeping the same graph does not produce a valid window decoder.
No speculative-boundary predictor or QPU feedback is executed by this bundle.

## Files

`manifest.csv` is the case index. Every folder under `cases/` contains:

| File | Purpose |
| --- | --- |
| `circuit.stim` | Full physical noisy circuit with detectors and observables |
| `decoder.dem` | Decomposed detector error model for graph construction |
| `measurements.b8` | Raw measurements for the full experiment |
| `detections.b8` | Detection events derived from those same raw measurements |
| `observables.b8` | Corresponding logical-observable flips |
| `block_graph.json` | TQEC logical surgery construction, including temporal pipes |
| `metadata.json` | Mask, noise, seeds, phase/round ranges, packet layout, bit widths, coordinates, hashes |
| `validation.json` | Paired conversion, noiseless checks, distance, physical-coupling checks, and decoding results |

Sample files use Stim `b8`: shot-major, little-endian bits, with each shot padded
to a byte boundary. Corresponding rows in all three files describe the same
shot. Each shot starts a new experiment; shots are not one continuous history.
Nominal spatial dimensions are SWIPER tile bounds (3×3 units per occupied tile),
not physical data-plus-ancilla dimensions. Physical bridge qubits occupy seams
between patches. Exact qubit and detector coordinates are in the circuits and
metadata. A missing tile stays absent; the tree does not cross missing tiles.

Noise is TQEC `NoiseModel.uniform_depolarizing(p=0.001)`: one/two-qubit Clifford
depolarization, idle depolarization, measurement flips, and reset errors.
It differs from the four-knob Stim memory-generator noise used in the separate
memory baseline. The generator is pinned to the TQEC commit in `dataset.json`.

## Load and decode

Only Stim, PyMatching, and NumPy are needed to use the saved files. TQEC is needed
only to regenerate them. Tested versions are in `requirements-decode.txt`.

```python
import csv
from pathlib import Path
import stim
import pymatching

root = Path(".")
with (root / "manifest.csv").open() as f:
    case = next(row for row in csv.DictReader(f) if row["coupled"] == "True")
folder = root / case["case"]
model = stim.DetectorErrorModel.from_file(folder / "decoder.dem")
dets = stim.read_shot_data_file(
    path=folder / "detections.b8", format="b8",
    num_detectors=model.num_detectors, bit_packed=True,
)
actual = stim.read_shot_data_file(
    path=folder / "observables.b8", format="b8",
    num_observables=model.num_observables, bit_packed=True,
)
matching = pymatching.Matching.from_detector_error_model(model)
prediction = matching.decode_batch(
    dets, bit_packed_shots=True, bit_packed_predictions=True,
)
```

Nonzero logical failures under noise are expected; `validation.json` records
failures after decoding all 1,000 shots. They do not indicate invalid files.

## Benchmark

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-decode.txt
.venv/bin/python benchmark_window_dataset.py . \
  --shots 1000 --repeats 3 --batch-sizes 1 32 256 \
  --out ../results/coupled-timing.csv
```

For example, `--case '*_bulk006_z_p0.001'` selects one repetition setting.
Graph construction is timed separately. Input loading, unpacking, graph build,
and warmup happen before decode timing. Batch size 1 uses unpacked `decode`;
larger batches use packed `decode_batch`. The CSV reports median/p95/p99 call
time, actual batch size, and amortized time per shot. Batch amortization is
throughput, not individual-shot response latency. Incomplete final batches are
omitted. Timing includes Python/native-call overhead.

For your own decoder, use its graph loader and decode calls with these same
files. A GPU/FPGA timer must synchronize completed work. These timings exclude
transport, QPU control, graph build, and raw-to-detector conversion; measure
those separately if needed for a round-trip latency model.

## Verify and regenerate

```bash
sha256sum -c checksums.sha256
python validate_coupled_dataset.py .
```

All saved shots were checked by repeating measurement-to-detector conversion.
Noiseless detectors and observables were checked, each merge was checked for
entangling gates on its bridge data, and each circuit's shortest graphlike
logical error was verified to have length 3. Every saved shot was also decoded
with PyMatching. Completion is recorded in `dataset.json` only after all cases
pass. These checks do not assert zero noisy logical errors or prove every
possible decoder boundary protocol correct.

The separate audit script also checks that each merge has a bridge data qubit
interacting with ancillas on **both** sides of the seam, confirms detector
release times against the actual measurement-record dependencies, verifies
the surgery tree stays within the footprint, and checks complete case coverage.

To regenerate into a new directory:

```bash
pip install -r requirements-generate.txt
python generate_coupled_window_dataset.py \
  --shapes base_shapes.json --out ../new-coupled-dataset
```

The generator refuses to overwrite an existing directory. `--shots`, `--p`,
`--seed`, and `--bulk-rounds` change the sweep. The recorded seeds require the
same Stim version and call pattern for reproducibility; they do not promise
identical samples across versions or platforms.

References:
[TQEC](https://github.com/tqec/tqec),
[TQEC compilation](https://tqec.github.io/tqec/user_guide/quick_start.html),
[Stim result formats](https://github.com/quantumlib/Stim/blob/main/doc/result_formats.md),
[PyMatching API](https://pymatching.readthedocs.io/en/stable/api.html).
