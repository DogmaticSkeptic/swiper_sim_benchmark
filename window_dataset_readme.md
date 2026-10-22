# Distance-3 window-footprint memory baseline

This is an offline decoder-input dataset covering the 19 spatial masks observed
in the supplied SWIPER reference runs. **Every occupied tile is an independent
distance-3 rotated surface-code memory patch.** The patches run in parallel and
are decoded together as a disjoint union. There is one logical observable per
patch. Different arrangements with the same number of patches have equivalent
graph topology, although bit ordering, display coordinates, and noise samples
can differ.

**These samples do not implement coupled lattice surgery or SWIPER's actual
interior decoding windows.** SWIPER's spatial bounds do not specify stabilizer
circuits, merge/split operations, or artificial temporal-boundary syndromes.
Each experiment here has fresh preparation and final readout boundaries.
Benchmark results are memory/throughput baselines; they are not validated latency
estimates for coupled surgery windows. Do not fit them to all SWIPER jobs without
accounting for that distinction. A later coupled dataset needs physical circuit
templates and the intended boundary/correction protocol.

## Contents

`dataset.json` gives the actual settings and completion status; `manifest.csv`
lists every case. Default settings produce 456 cases and 4,560,000 shots:

* 19 exact spatial masks, including rotations/reflections, from `base_shapes.json`.
* 1, 3, 6, 12, 24, and 48 syndrome-extraction rounds per shot.
* X-memory and Z-memory experiments (separate datasets).
* Noise probability 0.001 and 0.01. Each probability is applied separately to
  post-Clifford depolarization, start-of-round data depolarization, measurement
  flips, and reset flips. It is not a total error probability.
* 10,000 independent shots per case with a recorded deterministic seed.

Nominal names such as `6x9` describe SWIPER tile bounds, with 3x3 nominal units
per tile. They are not the physical data-plus-ancilla dimensions. Empty tiles
remain empty; the case metadata records the mask. Each patch contains nine data
qubits and eight ancillas. Stim qubit indices have gaps, so `num_qubits` is an
index upper bound, not the number of active qubits. Coordinate units in the
Stim circuits separate independent modules and differ from nominal tile units.

Each directory under `cases/` contains:

| File | Meaning |
| --- | --- |
| `circuit.stim` | Full noisy, detector-annotated Stim circuit |
| `decoder.dem` | Decomposed detector error model for decoder graph construction |
| `measurements.b8` | Raw measurements, round-major across patches, then final readout |
| `detections.b8` | Paired detection events derived from those same measurements |
| `observables.b8` | Paired logical-observable flips for scoring predictions |
| `metadata.json` | Exact mask, parameters, seeds, hashes, detector coordinates, per-patch index maps, and packet layout |
| `validation.json` | Full paired-conversion checks, noiseless check, and decoder smoke check |

All sample files use Stim's `b8` format: shots in consecutive rows, little-endian
bits within each byte, padding to a whole number of bytes **per shot**. Measurement,
detector, and observable rows correspond to the same experiment. The respective
bit counts and row widths are in metadata and the manifest. Do not concatenate
shots into a continuous syndrome history. Do not arbitrarily slice detector
vectors and use the original full graph to decode the slices.

`packets` partitions the measurements into syndrome rounds and final readout,
and lists the detectors available after each packet. Each packet contains all
patches' corresponding measurements. There is no assumed hardware round time
or transport latency. `patch_maps` gives the measurement/detector/observable
indices for each individual patch, if your decoder handles patches separately.
The round count includes syndrome extraction only; final data readout is extra.

## Load a case

```python
import csv
from pathlib import Path
import stim
import pymatching

root = Path(".")  # Dataset directory
with (root / "manifest.csv").open() as f:
    case = next(csv.DictReader(f))
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
decoder = pymatching.Matching.from_detector_error_model(model)
predicted = decoder.decode_batch(
    dets, bit_packed_shots=True, bit_packed_predictions=True,
)
# predicted and actual have the same packed observable layout.
```

## Run the reference benchmark

Create a Python environment and install `requirements.txt` (tested with Python
3.10). The data and Stim/DEM files can also be used by other compatible decoders.

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python benchmark_window_dataset.py . \
  --shots 1000 --repeats 3 --batch-sizes 1 32 256 \
  --out results/decoder-timing.csv
```

Filter cases with, for example, `--case '*_r012_z_p0.001'`. The script loads data,
constructs each graph, preprocesses, and warms up the decoder before timing.
It reports graph construction separately, and median/p95/p99 wall time for
individual calls. Batch size 1 uses unpacked `decode`; larger batches use packed
`decode_batch`. The latter's amortized time is a throughput metric, not an
individual-shot latency distribution. Remainder shots in incomplete batches are
excluded. Percentiles with few calls are only smoke measurements.

Timers include Python/native-call overhead. They exclude file I/O, graph
construction, unpacking, communication, and QPU control. For a GPU or FPGA,
replace the decoder calls and synchronize actual completion; report transport
and preprocessing costs separately. Pauli-frame maintenance and blocking-operation
feedback need their own timing model. No correction is sent to a QPU here.

The smoke check compares predictions with sampled logical flips; nonzero logical
failures are expected under noise and do not mean the files are inconsistent.
No claim about surgery fidelity or speculative-boundary accuracy is made.

## Verify or regenerate

From this directory, verify all original bundle files:

```bash
sha256sum -c checksums.sha256
```

The generator validates every saved shot by repeating measurement-to-detector
conversion, checks packing/file sizes and noiseless detectors/observables, and
decodes up to 128 shots per case with PyMatching. The completed dataset is marked
`complete: true` only after every case passes. `manifest.csv` records per-case
smoke failures. The sample files are immutable inputs; write timing results to
a separate path. This bundle has no dependence on the original SWIPER checkout.

The copied generator can reproduce the dataset into a new directory:

```bash
python generate_window_dataset.py --shapes base_shapes.json --out ../new-dataset
```

For custom settings, use `--rounds 3 6 12`, `--shots 100000`, `--bases z`, or
`--noise 0.001`. It refuses to overwrite an existing directory. Reproducibility
requires the recorded Stim version and the same sampling call pattern; seeds
alone are not a promise of identical output across Stim versions or platforms.

Format and API references:
[Stim result formats](https://github.com/quantumlib/Stim/blob/main/doc/result_formats.md),
[Stim surface-code generator](https://github.com/quantumlib/Stim/blob/main/src/stim/gen/gen_surface_code.cc),
[PyMatching decoding API](https://pymatching.readthedocs.io/en/stable/api.html).
