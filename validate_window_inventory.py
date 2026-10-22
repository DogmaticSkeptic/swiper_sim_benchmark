"""Independently verify the distance-3 reference inventory and its exports."""

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path

parser = argparse.ArgumentParser()
root = Path(__file__).resolve().parent
parser.add_argument('--output', type=Path, default=root / 'window-inventory/d3')
parser.add_argument('--sources', type=Path, default=root / 'swiper-sim/benchmarks/cached_schedules')
parser.add_argument('--require-complete', action='store_true')
args = parser.parse_args()
out = args.output
sources = args.sources
paths = {p.relative_to(sources).with_suffix('').as_posix(): p for p in sources.rglob('*.lss')}
expected = {(name, method) for name in paths for method in ('sliding', 'parallel', 'aligned')}
completed = set()
base_counts = Counter()
core_counts = Counter()
source_hashes = {}
total = 0
for file in sorted((out / 'runs').glob('*.json')):
    run = json.loads(file.read_text())
    key = (run['benchmark'], run['method'])
    assert key not in completed and key in expected, key
    completed.add(key)
    path = paths[run['benchmark']]
    digest = source_hashes.setdefault(run['benchmark'], hashlib.sha256(path.read_bytes()).hexdigest())
    assert digest == run['source_sha256'], key
    assert run['distance'] == 3 and run['decode_rounds'] == 1
    assert run['decoders'] is None and run['speculation_mode'] is None and run['seed'] == 0
    assert run['instructions'] == len(path.read_text().strip().splitlines()), key
    assert sum(size['occurrences'] for size in run['sizes']) == run['window_count'], key
    assert sum(shape['occurrences'] for shape in run['shapes']) == run['window_count'], key
    total += run['window_count']
    for shape in run['shapes']:
        encoded = json.dumps(shape['regions'], separators=(',', ':'))
        assert hashlib.sha256(encoded.encode()).hexdigest()[:20] == shape['shape_id'], key
        patches = tuple(sorted({(r[1], r[2]) for r in shape['regions']}))
        assert min(p[0] for p in patches) == min(p[1] for p in patches) == 0
        assert len(patches) == shape['total_distinct_patches']
        assert 3 * (max(p[0] for p in patches) + 1) == shape['nominal_bbox_data_rows']
        assert 3 * (max(p[1] for p in patches) + 1) == shape['nominal_bbox_data_cols']
        assert sum(r[4] for r in shape['regions']) == shape['total_patch_rounds']
        base_counts[patches] += shape['occurrences']
        core = {(r[1], r[2]) for r in shape['regions'] if r[0] == 'commit'}
        row0, col0 = min(p[0] for p in core), min(p[1] for p in core)
        core_counts[tuple(sorted((r-row0, c-col0) for r,c in core))] += shape['occurrences']

exported = json.loads((out / 'base_shapes.json').read_text())
assert Counter({tuple(map(tuple, shape['occupied_patches'])): shape['occurrences'] for shape in exported}) == base_counts
cores = json.loads((out / 'commit_base_shapes.json').read_text())
assert Counter({tuple(map(tuple, shape['occupied_patches'])): shape['occurrences'] for shape in cores}) == core_counts
with (out / 'base_sizes_by_benchmark.csv').open() as file:
    rows = list(csv.DictReader(file))
assert sum(int(row['occurrences']) for row in rows) == total
assert {(row['benchmark'], row['method']) for row in rows} == completed
with (out / 'base_bounding_boxes.csv').open() as file:
    boxes = {(int(row['nominal_data_rows']), int(row['nominal_data_cols'])) for row in csv.DictReader(file)}
assert boxes == {(3*(max(r for r,c in mask)+1), 3*(max(c for r,c in mask)+1)) for mask in base_counts}
missing = sorted(expected - completed)
if args.require_complete:
    assert not missing, missing
    assert json.loads((out / 'failures.json').read_text()) == []
    coverage = json.loads((out / 'coverage.json').read_text())
    assert coverage['expected_runs'] == coverage['completed_runs'] == len(expected)
    assert coverage['missing_runs'] == []
facts = {
    'validated_runs': len(completed), 'expected_runs': len(expected),
    'schedule_files': len(paths), 'unique_source_traces': len(set(source_hashes.values())),
    'constructed_windows': total, 'spatial_masks': len(base_counts),
    'spatial_bounding_boxes': sorted(boxes), 'core_masks': len(core_counts),
    'source_hashes_and_instruction_counts_match': True,
    'per_run_and_export_histograms_match': True, 'missing_runs': missing,
}
print(json.dumps(facts, indent=2))
if args.require_complete:
    validation = json.loads((out / 'validation.json').read_text())
    validation['inventory_validation'] = facts
    (out / 'validation.json').write_text(json.dumps(validation, indent=2)+'\n')
