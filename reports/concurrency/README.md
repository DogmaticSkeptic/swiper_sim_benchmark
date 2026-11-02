# Saved concurrency benchmark results

This snapshot preserves the default distance-3 sweep over 45 benchmarks and
three window policies. QEC round duration is 1 µs, seeds are 0, speculation is
off, and the timing profile is the supplied CPU PyMatching reference.

- [Mean heatmaps across benchmarks (PNG)](average_benchmarks.png),
  [PDF](average_benchmarks.pdf), [editable SVG](average_benchmarks.svg).
- [Individual benchmark heatmaps (combined PDF)](all_benchmarks.pdf).
- [Comparison atlas (PDF)](comparison_atlas.pdf).
- [Raw sweep table](sweep.csv), [original configuration](configuration.json),
  and [run summary](summary.json).

The speed panels hold α = 0; the slowdown panels hold s = 1. Colors represent
additional idle patch-rounds relative to a policy-specific baseline with
unlimited slots, no contention, and one-round decoding. These are scheduling
results using representative CPU timings.

All 9,855 expected simulation outcomes were recorded. Of these, 5,857 completed
successfully; 2,585 hit the backlog cutoff, 1,394 hit the time cutoff, 14 hit the
memory cutoff, 3 had process failures, and 2 hit the watchdog cutoff. Missing
results are excluded from aggregate means. Each aggregate cell shows its valid
benchmark count out of 45. Contributors differ between cells; in particular,
the sliding-policy slowdown panels have only 3–5 valid benchmarks per cell.

The saved configuration and CSV retain original source hashes and profile paths
as provenance. They describe the recorded experiment before configurable sweep
grids were added. Read the [main README](../../README.md) for fresh simulations,
parameter definitions, and hardware calibration. Use a new output directory
for a fresh run with the current simulation source.

To regenerate plots from this snapshot without running simulations, copy its
configuration and CSV to a local results directory, then run the plotting tools
from the repository root:

```bash
mkdir -p results/saved-report
cp reports/concurrency/configuration.json reports/concurrency/sweep.csv results/saved-report/
.venv/bin/python plot_concurrency.py results/saved-report
.venv/bin/python plot_aggregate_concurrency.py results/saved-report
```

Per-cell scratch JSON, logs, environments, and caches remain local. The combined
PDFs provide the individual plots without storing every per-benchmark format.
