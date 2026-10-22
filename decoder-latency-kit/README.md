# SWIPER latency-function kit

`swiper_latency.load_latency_fn` returns the callable SWIPER needs:

```python
decoding_latency_fn(volume_in_patch_rounds) -> positive_integer_QEC_rounds
```

The adapter, benchmark driver, fitting pipeline, and runnable reference profile
are ready. The included profile measures **PyMatching on this workspace's ARM
CPU**, using the 114 representative coupled-surgery cases, 1,000 shots per case.
It is an approximate volume model, not a calibration of your collaborator's
GPU/FPGA or of exact SWIPER interior windows. The hardware and scope are embedded
in the profile. Full surgery experiments include preparation and context, so
their nominal patch-round count is a proxy for the callback's workload.

## Use the function

From the parent workspace, with the kit at `decoder-latency-kit/`:

```python
from swiper_latency import load_latency_fn

latency_fn = load_latency_fn(
    "decoder-latency-kit/profiles/cpu-pymatching-d3.json",
    distance=3,
    round_us=1.0,        # Set this to your QPU's actual syndrome-round duration.
    statistic="median", # Also supports mean, p95, and p99.
)

# Pass this directly into your existing DecodingSimulator.run(...):
# decoding_latency_fn=latency_fn
print(latency_fn(6))
```

The function's input is the sum of durations of all commit and buffer regions.
**Do not multiply this input by d².** A duration of three rounds on two patches
has volume six. The same volume can correspond to different shapes. The stock
callback cannot distinguish them. The output is `ceil(fitted_microseconds /
round_us)`, with a minimum of one round for SWIPER's countdown. The adapter
rejects a profile for a different code distance.

The adapter itself uses only the Python standard library. Put `swiper_latency.py`
on your Python import path, or run from the workspace/kit directory.

The workspace demo is already wired to load a profile:

```bash
.venv/bin/python run_swiper_demo.py \
  --latency-profile decoder-latency-kit/profiles/cpu-pymatching-d3.json \
  --round-us 1 --decoders 4
```

Distance defaults to the profile's distance (3 here). It runs the same logical
program with and without speculation. To use a compiled benchmark, add
`--schedule swiper-sim/benchmarks/cached_schedules/toffoli.lss`. Select
`--method sliding`, `parallel`, or `aligned`. Output includes the profile
provenance and the actual volumes requested. `demo-cpu-reference.json` and
`demo-toffoli-reference.json` in this kit record verified example runs.

## Produce your collaborator's profile

Run these commands **from the kit directory**, pointing at the unpacked coupled
dataset from the previous bundle. Python 3.10 was used here.

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt

.venv/bin/python benchmark_decoder_latency.py ../decoder-benchmark-data/d3-coupled-surgery \
  --out my-decoder.csv --shots 1000 --backend my_decoder_adapter:build

.venv/bin/python fit_decoder_latency.py \
  --timings my-decoder.csv \
  --manifest ../decoder-benchmark-data/d3-coupled-surgery/manifest.csv \
  --out profiles/my-decoder-d3.json
```

Replace the profile path in `load_latency_fn` with the new JSON file. To time the
CPU reference again, use `--backend pymatching`. `--repeats` defaults to one;
increasing it re-times the same 1,000 inputs, rather than generating new shots.
`--raw-out individual-calls.csv` optionally records every call duration.

Supply an importable `my_decoder_adapter.py` with this contract:

```python
def build(detector_error_model, metadata):
    # Construct/upload your decoder graph here, outside timed calls.
    def decode(syndrome):
        # syndrome: uint8 array of unpacked detector bits for ONE complete case.
        # Run the decoder and wait for actual completion, including accelerator
        # synchronization. Return materialized, unpacked 0/1 logical predictions,
        # one per detector_error_model.num_observables.
        return your_synchronous_decoder(syndrome)
    return decode
```

Graph construction is measured separately. File loading, bit unpacking, warmup,
and scoring are outside the timed call. Input staging, accelerator transfer, or
output copies inside your adapter are included; document those with
`--timing-scope '...'`. An asynchronous kernel-submission time is not a decode
completion latency. The helper validates output shape/bit values and compares
predictions with the paired observables. Noisy logical failures are recorded;
they do not themselves invalidate a timing sample.

If you use a separate benchmark harness, export one row per case with:

```
case,mode,batch_size,median_call_us,p95_call_us,timing_scope
cases/EXACT_DIRECTORY_NAME,decode_unpacked_single,1,12.3,18.4,synchronous decoder completion
```

Copy exact case IDs from `manifest.csv`. Additional `mean_call_us` and
`p99_call_us` columns are supported. All cases are required by default. The fitter
rejects duplicate/unknown cases, mixed distances, nonfinite values, and mixed
timing scopes. It excludes batched-throughput rows. `--allow-partial` records
deliberate missing-case coverage, but does not make the fit complete.

For datasets of actual commit/buffer windows, provide `total_patch_rounds` in the
manifest to use the sum of those region durations directly. All cases must
provide that column or all must omit it. Otherwise the fitter uses the full
experiment proxy `patches * rounds`. An explicit volume field does not itself
verify the circuit's physical boundary protocol.

## What the fitted function assumes

The fitter uses nominal occupied-patch count × actual full-circuit measurement
rounds as its volume proxy. It averages equal-volume case statistics with equal
weight per case, then fits a nondecreasing curve by isotonic least squares and
linearly interpolates between fitted volumes. It preserves measured small-job
costs rather than letting the largest cases dominate one global straight line.

Below the smallest measured proxy volume, the model holds the smallest fitted
cost constant. Above the largest volume, it scales proportionally from the last
knot. These are extrapolation assumptions. `reference-volume-predictions.csv`
shows predictions and support status for every callback volume observed in the
distance-3 reference inventory. Even an exact proxy-volume match is not proof
of matching physical window geometry.

The reference source volumes span 3–4,606 patch-rounds; the saved SWIPER reference
jobs request 1–18 and 21. Volumes 1 and 2 use the lower constant extrapolation.
Within-range predictions often interpolate between available cases. The JSON
records fitting errors and leave-one-footprint-out validation errors; these
describe the surrogate's statistical fit to these measurements, not correctness
of an interior-window decoder or fidelity of the quantum algorithm.

Median/mean curves are deterministic timing assumptions. The fitted p95/p99
curves average case percentile statistics before smoothing; they are sensitivity
scenarios, not global mixture percentiles or random tail-latency samples.
Frequencies of geometries in the actual workload are not used as fit weights.

`per_job_overhead_us=` adds a fixed overhead to **every** job. Include only costs
that occupy each modeled decoder slot. A feedback delay that applies only at
blocking operations cannot be represented correctly as a per-job constant;
that requires a separate feedback model. Shared-link contention, accelerator
batching, concurrency-dependent slowdown, and real speculative-boundary work
also need additional modeling. SWIPER still handles the scheduling, decoder-pool
limits, windows, and its configured speculation probabilities.

The earlier `swiper-calibration-coverage.json` remains valid: exact physical
SWIPER commit/buffer windows and their decoder-specific boundary protocol have
not been reconstructed. The software path to fit and use a volume callback is
complete; calibration of the target hardware and exact window protocol remains
an experimental input.

## Bundle

`reference-single-job.csv` and its environment JSON contain the measured CPU
inputs to the included profile. `source-manifest.csv` maps those timings to
volumes. `validation.json` records software checks. Verify files with:

```bash
sha256sum -c checksums.sha256
```
