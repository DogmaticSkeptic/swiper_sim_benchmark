"""Inventory actual SWIPER decoding-window sizes across supplied schedules."""

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
import csv
import ctypes
import datetime as dt
import fcntl
import gc
import hashlib
import json
from pathlib import Path
import time
import traceback
import networkx as nx

from swiper.device_manager import DeviceManager
from swiper.decoder_manager import DecoderManager
from swiper.lattice_surgery_schedule import LatticeSurgerySchedule
from swiper.simulator import DecodingSimulator
from swiper.window_builder import DecodingWindow
import swiper.simulator as simulator_module

UPSTREAM_SHARES_BOUNDARY = DecodingWindow.shares_boundary


def trim_heap():
    gc.collect()
    trim = getattr(ctypes.CDLL(None), "malloc_trim", None)
    if trim is not None:
        trim.argtypes = [ctypes.c_size_t]
        trim.restype = ctypes.c_int
        trim(0)


def patch_candidates(window):
    cached = window.__dict__.get("_inventory_patch_candidates")
    if cached is not None:
        return cached
    occupied = frozenset(region.patch for region in window.commit_region)
    candidates = set(occupied)
    for region in window.commit_region:
        instruction = region.merge_instr
        if instruction is None:
            continue
        neighbors = instruction.__dict__.get("_inventory_patch_neighbors")
        if neighbors is None:
            neighbors = {}
            for first, second in instruction.merge_faces:
                neighbors.setdefault(first, set()).add(second)
                neighbors.setdefault(second, set()).add(first)
            instruction.__dict__["_inventory_patch_neighbors"] = neighbors
        candidates.update(neighbors.get(region.patch, ()))
    cached = (occupied, frozenset(candidates))
    window.__dict__["_inventory_patch_candidates"] = cached
    return cached


def filtered_shares_boundary(window, other):
    # A boundary requires either the same patch or an explicitly connected
    # surgery patch. Reject spatially unrelated windows before the exact check.
    if patch_candidates(window)[1].isdisjoint(patch_candidates(other)[0]):
        return False
    return UPSTREAM_SHARES_BOUNDARY(window, other)


class InventoryDecoderManager(DecoderManager):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.retirement_candidates = set()
        self.retired_task_count = 0

    def step(self):
        completed = super().step()
        self.retirement_candidates.update(completed)
        return completed


class InventoryDeviceManager(DeviceManager):
    """Avoid solving far-future schedules before their earliest possible start."""

    def _predict_instruction_start_times(self):
        if (not hasattr(self, "_inventory_bound_completed")
                or self._completed_instruction_count - self._inventory_bound_completed >= 256):
            if not hasattr(self, "_inventory_topological_order"):
                self._inventory_topological_order = list(nx.topological_sort(self.schedule_dag))
            earliest = {}
            for index in self._inventory_topological_order:
                if index in self._active_instructions:
                    earliest[index] = self.current_round + self._active_instructions[index] - self._instruction_durations[index]
                elif self.schedule_instructions[index].end_round != -1:
                    earliest[index] = self.schedule_instructions[index].end_round - self._instruction_durations[index] + 1
                elif not self._is_startup_instruction[index]:
                    earliest[index] = max(self.current_round, max((
                        earliest[parent] + self._instruction_durations[parent]
                        for parent in self.schedule_dag.predecessors(index)
                        if not self._is_startup_instruction[parent]
                    ), default=0))
                else:
                    earliest[index] = 0
            for index in self._inventory_topological_order:
                if (self._is_startup_instruction[index]
                        and index not in self._active_instructions
                        and self.schedule_instructions[index].end_round == -1):
                    earliest[index] = max(0, min((
                        earliest[child] - self._instruction_durations[index]
                        for child in self.schedule_dag.successors(index)
                    ), default=0))
            self._inventory_earliest_starts = earliest
            self._inventory_bound_completed = self._completed_instruction_count
        manager = self

        class PredictionTimes(dict):
            def __missing__(self, index):
                # Return a safe future bound to the dispatch check, without
                # storing it. The upstream dependency solver only trusts values
                # actually stored in this dict, so it still computes exact
                # predictions whenever another prediction depends on this node.
                bound = manager._inventory_earliest_starts[index]
                if bound > manager.current_round:
                    return bound
                manager._predict_instruction_start_time_fully(index, self)
                return self[index]

        predictions = PredictionTimes()
        for index in self._instruction_frontier:
            if self._inventory_earliest_starts[index] <= self.current_round:
                self._predict_instruction_start_time_fully(index, predictions)
        return predictions


class InventorySchedule(LatticeSurgerySchedule):
    """Use incremental graph construction with the default parser's edge order."""

    def __copy__(self):
        # Preserve upstream deep-copy behavior, including frozenset iteration
        # order: window-merging tie breaks can depend on that order.
        copied = super().__copy__()
        copied.__class__ = type(self)
        return copied

    def to_dag(self, d=None):
        graph = super().to_dag(d)
        # The default backward scan inserts predecessors in descending index
        # order. Preserve that order because tied scheduling decisions may use it.
        for predecessors in graph._pred.values():
            ordered = sorted(predecessors.items(), reverse=True)
            predecessors.clear()
            predecessors.update(ordered)
        return graph


def region_shape(window):
    """Keep geometry/roles and lifecycle flags; normalize only space/time origin."""
    regions = [("commit", r) for r in window.commit_region]
    regions += [("buffer", r) for r in window.buffer_regions]
    row0 = min(r.patch[0] for _, r in regions)
    col0 = min(r.patch[1] for _, r in regions)
    time0 = min(r.round_start for _, r in regions)
    shape = sorted((
        role, r.patch[0] - row0, r.patch[1] - col0,
        r.round_start - time0, r.duration, r.num_spatial_boundaries,
        r.initialized_patch, r.discard_after, r.prior_t,
        sorted((a[0] - row0, a[1] - col0, b[0] - row0, b[1] - col0)
               for a, b in r.merge_instr.merge_faces) if r.merge_instr else [],
    ) for role, r in regions)
    return json.dumps(shape, separators=(",", ":"))


def window_size(window, distance):
    regions = list(window.commit_region) + list(window.buffer_regions)
    patches = {r.patch for r in regions}
    rows = max(p[0] for p in patches) - min(p[0] for p in patches) + 1
    cols = max(p[1] for p in patches) - min(p[1] for p in patches) + 1
    span = max(r.round_start + r.duration for r in regions) - min(r.round_start for r in regions)
    return {
        "commit_patch_count": len({r.patch for r in window.commit_region}),
        "buffer_patch_count": len({r.patch for r in window.buffer_regions}),
        "total_distinct_patches": len(patches),
        "bbox_patch_rows": rows, "bbox_patch_cols": cols,
        "nominal_bbox_data_rows": rows * distance,
        "nominal_bbox_data_cols": cols * distance,
        "time_span_rounds": span,
        "commit_patch_rounds": window.commit_spacetime_volume(),
        "buffer_patch_rounds": window.buffer_spacetime_volume(),
        "total_patch_rounds": window.total_spacetime_volume(),
        "nominal_d2_scaled_volume": distance**2 * window.total_spacetime_volume(),
    }


class InventorySimulator(DecodingSimulator):
    def __init__(self, distance, fast_prediction=False, retire_history=False, initialization_lock=None):
        super().__init__()
        self.distance = distance
        self.fast_prediction = fast_prediction
        self.retire_history = retire_history
        self.initialization_lock = initialization_lock
        self.sizes = Counter()
        self.shapes = Counter()
        self.shape_sizes = {}

    def initialize_experiment(self, **kwargs):
        if self.initialization_lock is None:
            return self._initialize_inventory(**kwargs)
        # Compilation/deep-copy peaks are higher than steady simulation usage.
        # Serialize these peaks while allowing the actual runs to overlap.
        with Path(self.initialization_lock).open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return self._initialize_inventory(**kwargs)

    def _initialize_inventory(self, **kwargs):
        original_device_class = simulator_module.DeviceManager
        original_decoder_class = simulator_module.DecoderManager
        try:
            if self.fast_prediction:
                simulator_module.DeviceManager = InventoryDeviceManager
            if self.retire_history:
                if kwargs.get("speculation_mode") or kwargs.get("lightweight_setting") != 2:
                    raise ValueError("History retirement requires no speculation and lightweight_setting=2")
                simulator_module.DecoderManager = InventoryDecoderManager
            super().initialize_experiment(**kwargs)
        finally:
            simulator_module.DeviceManager = original_device_class
            simulator_module.DecoderManager = original_decoder_class
        if self.retire_history:
            # DeviceManager has already copied its executable DAG and patch
            # lifecycle information. Its private schedule's compilation caches
            # are no longer read by the simulation loop.
            self._device_manager.schedule._generated_dag.clear()
            self._device_manager.schedule._instructions_by_patch.clear()
            source = kwargs["schedule"]
            if getattr(source, "inventory_consumable", False):
                # This schedule was loaded privately for this one inventory
                # run. DeviceManager owns its deep copy and both execution DAGs
                # are built; the input compilation objects can now be released.
                source.instructions.clear()
                source._generated_dag.clear()
                source._instructions_by_patch.clear()
                trim_heap()
        original = self._window_manager.process_round

        def capture(new_rounds):
            boundary_method = DecodingWindow.shares_boundary
            try:
                if self.fast_prediction:
                    DecodingWindow.shares_boundary = filtered_shares_boundary
                windows = original(new_rounds)
            finally:
                DecodingWindow.shares_boundary = boundary_method
            for window in windows:
                size = window_size(window, self.distance)
                self.sizes[tuple(size.items())] += 1
                shape = region_shape(window)
                self.shapes[shape] += 1
                self.shape_sizes[shape] = size
            return windows

        self._window_manager.process_round = capture

    def step_experiment(self, *args, **kwargs):
        super().step_experiment(*args, **kwargs)
        if not self.retire_history or self._decoding_manager._current_round % 128:
            return
        decoder = self._decoding_manager
        manager = self._window_manager
        # Remove only finalized, verified windows with every adjacent task also
        # verified. No pending task can depend on them, and constructed windows
        # have all their neighbors fixed when speculation is disabled.
        for index in list(decoder.retirement_candidates):
            task = decoder._get_task_or_none(index)
            if task is None:
                decoder.retirement_candidates.discard(index)
                continue
            if not decoder._is_verified_task(index):
                continue
            adjacent = set(manager.window_dag.predecessors(index)) | set(manager.window_dag.successors(index))
            if any(decoder._get_task_or_none(other) is None or not decoder._is_verified_task(other)
                   for other in adjacent):
                continue
            for instruction in task.window.parent_instr_idx:
                references = decoder._instruction_tasks.get(instruction)
                if references is not None:
                    references.discard(index)
                    if not references:
                        del decoder._instruction_tasks[instruction]
            if manager.all_windows[index] is not None:
                manager._remove_window(index)
            else:
                manager.window_dag.remove_node(index)
            decoder._tasks_by_idx[index] = None
            decoder.retirement_candidates.discard(index)
            decoder.retired_task_count += 1


def inventory(path, relative, method, args):
    started = time.monotonic()
    simulator = InventorySimulator(args.distance, fast_prediction=True, retire_history=True,
                                   initialization_lock=args.out / ".initialization.lock")

    def latency(_volume):
        return args.decode_rounds

    # Both upstream modes generate the same dependencies. Incremental graph
    # construction avoids the quadratic backward scan used by the default.
    schedule = InventorySchedule.from_str(
        path.read_text().strip(), generate_dag_incrementally=True,
    )
    instruction_count = len(schedule.instructions)
    schedule.inventory_consumable = True
    ok, _, device, _, decoder = simulator.run(
        schedule=schedule, distance=args.distance, scheduling_method=method,
        decoding_latency_fn=latency, max_parallel_processes=args.decoders,
        speculation_mode=None, rng=args.seed, lightweight_setting=2,
        clock_timeout=dt.timedelta(seconds=args.timeout) if args.timeout else None,
        device_rounds_cutoff=args.round_cutoff,
        print_interval=dt.timedelta(seconds=45),
    )
    if not ok:
        raise RuntimeError("Simulation hit a configured cutoff; no complete inventory saved")
    count = sum(simulator.sizes.values())
    if count != decoder.num_completed_windows:
        raise AssertionError((count, decoder.num_completed_windows))
    result = {
        "benchmark": relative, "method": method,
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "distance": args.distance, "decode_rounds": args.decode_rounds,
        "decoders": args.decoders, "seed": args.seed,
        "speculation_mode": None,
        "instructions": instruction_count,
        "device_rounds": device.num_rounds,
        "decoder_rounds": decoder.num_rounds,
        "window_count": count, "elapsed_seconds": time.monotonic() - started,
        "retired_verified_tasks": getattr(simulator._decoding_manager, "retired_task_count", 0),
        "sizes": [dict(key, occurrences=count) for key, count in sorted(simulator.sizes.items())],
        "shapes": [{
            "shape_id": hashlib.sha256(shape.encode()).hexdigest()[:20],
            "regions": json.loads(shape), "occurrences": count,
            **simulator.shape_sizes[shape],
        } for shape, count in sorted(simulator.shapes.items())],
    }
    # The recording closure captures this simulator; release it before this
    # worker loads another large program.
    del simulator._window_manager.process_round
    del simulator
    trim_heap()
    return result


def write_csv(path, rows, fields=None):
    if not rows:
        return
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields or list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def spatial_report(out, schedules, methods, selected=None):
    files = sorted(schedules.rglob("*.lss"))
    names = [p.relative_to(schedules).with_suffix("").as_posix() for p in files]
    if selected:
        names = [name for name in names if name in selected]
    expected = {(name, method) for name in names for method in methods}
    runs = [json.loads(p.read_text()) for p in (out / "runs").glob("*.json")]
    completed = {(run["benchmark"], run["method"]) for run in runs}
    coverage = {
        "expected_runs": len(expected), "completed_runs": len(completed & expected),
        "missing_runs": sorted(expected - completed),
        "schedule_files": len(names),
        "unique_source_traces": len({run["source_sha256"] for run in runs}),
    }
    (out / "coverage.json").write_text(json.dumps(coverage, indent=2) + "\n")
    shapes = json.loads((out / "base_shapes.json").read_text())
    dimensions = {}
    for shape in shapes:
        key = (shape["nominal_data_rows"], shape["nominal_data_cols"])
        entry = dimensions.setdefault(key, {"occupied_counts": set(), "shape_count": 0,
                                             "rectangular_observed": False})
        entry["occupied_counts"].add(shape["occupied_patch_count"])
        entry["shape_count"] += 1
        entry["rectangular_observed"] |= shape["rectangular_footprint"]
    dimension_rows = [{
        "nominal_data_rows": rows, "nominal_data_cols": cols,
        "occupied_patch_counts": " ".join(map(str, sorted(entry["occupied_counts"]))),
        "distinct_spatial_shapes": entry["shape_count"],
        "filled_rectangle_observed": entry["rectangular_observed"],
    } for (rows, cols), entry in sorted(dimensions.items())]
    write_csv(out / "base_bounding_boxes.csv", dimension_rows)
    with (out / "commit_base_sizes.csv").open() as file:
        core_dimensions = sorted({(int(row["nominal_data_rows"]), int(row["nominal_data_cols"]))
                                  for row in csv.DictReader(file)})
    per_benchmark = {}
    with (out / "base_sizes_by_benchmark.csv").open() as file:
        for row in csv.DictReader(file):
            per_benchmark.setdefault(row["benchmark"], set()).add(
                (int(row["nominal_data_rows"]), int(row["nominal_data_cols"])))
    lines = [
        "# Distance-3 spatial base inventory", "",
        f'Coverage: {coverage["completed_runs"]}/{coverage["expected_runs"]} runs across '
        f'{coverage["schedule_files"]} supplied schedule files and {len(methods)} window policies.', "",
        "Configuration: d=3, one-round decoding, unlimited decoder slots, no speculation, seed 0.", "",
        "Commit-only bounding boxes (without buffers): "
        + ", ".join(f"{rows}×{cols}" for rows, cols in core_dimensions) + ".", "",
        "Every basic tile is 3×3 in the nominal data-grid model. The dimensions below are bounding boxes "
        "of all commit and buffer patches in a decoding window, with the temporal extent ignored. "
        "They are not physical data-plus-ancilla dimensions. A bounding box may contain unoccupied tiles; "
        "the exact patch masks are in `base_shapes.json`. A projected footprint may combine patches from different rounds.", "",
        f'Found **{len(dimensions)} bounding-box dimensions** and **{len(shapes)} distinct spatial masks**.', "",
        "| Nominal bounding box | Observed occupied-patch counts | Spatial masks | Filled rectangle observed |",
        "| --- | --- | --- | --- |",
    ]
    for row in dimension_rows:
        lines.append(f'| {row["nominal_data_rows"]}×{row["nominal_data_cols"]} | {row["occupied_patch_counts"]} | {row["distinct_spatial_shapes"]} | {"yes" if row["filled_rectangle_observed"] else "no"} |')
    lines += ["", "## Per benchmark: union of sliding, parallel, and aligned policies", "",
              "| Benchmark schedule | Spatial bounding boxes |", "| --- | --- |"]
    for benchmark, boxes in sorted(per_benchmark.items()):
        lines.append(f'| {benchmark} | {", ".join(f"{r}×{c}" for r,c in sorted(boxes))} |')
    lines += ["", "## Files", "",
              "* `base_bounding_boxes.csv`: compact list of spatial dimensions, ignoring rounds.",
              "* `base_sizes.csv`: dimensions plus occupied-patch count and occurrence count.",
              "* `base_sizes_by_benchmark.csv`: spatial sizes separated by algorithm and policy.",
              "* `base_shapes.json`: exact normalized occupied-patch coordinates for each mask.",
              "* `commit_base_sizes.csv` and `commit_base_shapes.json`: core spatial footprints without buffers.",
              "* `coverage.json`: expected/completed runs and any missing runs.",
              "* `runs/*.json`: source hashes, full window-region geometry, and per-run results.", "",
              "Only translation is normalized: rotations and reflections are kept distinct. "
              "Counts count constructed windows across the supplied files and policies, including the duplicate "
              "random-T source file. This is a complete inventory for these reference runs, not all possible "
              "latencies, pools, seeds, or speculation outcomes. Use the region-level geometry and surgery protocol "
              "to construct decoder circuits; a spatial bounding box alone does not specify a decoding graph.", ""]
    (out / "README.md").write_text("\n".join(lines))


def summarize(out):
    runs = [json.loads(p.read_text()) for p in sorted((out / "runs").glob("*.json"))]
    summaries, sizes, shapes = [], [], []
    global_sizes = Counter()
    global_shapes = {}
    base_shapes = {}
    bases_by_benchmark = []
    for run in runs:
        s = run["sizes"]
        summaries.append({
            **{k: run[k] for k in ("benchmark", "method", "distance", "window_count", "device_rounds")},
            "unique_size_combinations": len(s),
            "unique_region_shapes": len(run["shapes"]),
            "max_distinct_patches": max(v["total_distinct_patches"] for v in s),
            "max_time_span_rounds": max(v["time_span_rounds"] for v in s),
            "min_total_patch_rounds": min(v["total_patch_rounds"] for v in s),
            "max_total_patch_rounds": max(v["total_patch_rounds"] for v in s),
            "all_total_patch_rounds": " ".join(map(str, sorted({v["total_patch_rounds"] for v in s}))),
        })
        for size in s:
            sizes.append({"benchmark": run["benchmark"], "method": run["method"], **size})
            key = tuple((k, v) for k, v in size.items() if k != "occurrences")
            global_sizes[key] += size["occurrences"]
        for shape in run["shapes"]:
            row = {"benchmark": run["benchmark"], "method": run["method"], **shape}
            row["regions"] = json.dumps(row["regions"], separators=(",", ":"))
            shapes.append(row)
            key = shape["shape_id"]
            if key not in global_shapes:
                global_shapes[key] = {**shape, "occurrences": 0, "runs": []}
            global_shapes[key]["occurrences"] += shape["occurrences"]
            global_shapes[key]["runs"].append(f'{run["benchmark"]}:{run["method"]}')
        run_bases = Counter()
        for shape in run["shapes"]:
            patches = sorted({(region[1], region[2]) for region in shape["regions"]})
            base_key = json.dumps(patches, separators=(",", ":"))
            rows = max(p[0] for p in patches) + 1
            cols = max(p[1] for p in patches) + 1
            distance = run["distance"]
            base_size = {
                "bbox_patch_rows": rows, "bbox_patch_cols": cols,
                "nominal_data_rows": distance * rows,
                "nominal_data_cols": distance * cols,
                "occupied_patch_count": len(patches),
                "nominal_occupied_data_area": distance**2 * len(patches),
                "rectangular_footprint": len(patches) == rows * cols,
            }
            if base_key not in base_shapes:
                base_shapes[base_key] = {
                    "base_shape_id": hashlib.sha256(base_key.encode()).hexdigest()[:20],
                    **base_size, "occupied_patches": patches,
                    "occurrences": 0, "runs": set(),
                }
            base_shapes[base_key]["occurrences"] += shape["occurrences"]
            base_shapes[base_key]["runs"].add(f'{run["benchmark"]}:{run["method"]}')
            run_bases[tuple(base_size.items())] += shape["occurrences"]
        bases_by_benchmark.extend({"benchmark": run["benchmark"], "method": run["method"],
                                   **dict(key), "occurrences": count}
                                  for key, count in sorted(run_bases.items()))
    write_csv(out / "benchmark_summary.csv", summaries)
    write_csv(out / "window_sizes_by_benchmark.csv", sizes)
    write_csv(out / "window_shapes_by_benchmark.csv", shapes)
    union = [dict(key, occurrences=count) for key, count in sorted(global_sizes.items())]
    write_csv(out / "all_window_sizes.csv", union)
    (out / "all_window_shapes.json").write_text(json.dumps(list(global_shapes.values()), indent=2) + "\n")
    volumes = sorted({row["total_patch_rounds"] for row in union})
    (out / "all_callback_volumes.json").write_text(json.dumps(volumes) + "\n")
    base_list = []
    base_sizes = Counter()
    for value in base_shapes.values():
        value["runs"] = sorted(value["runs"])
        base_list.append(value)
        key = tuple((k, value[k]) for k in ("bbox_patch_rows", "bbox_patch_cols", "nominal_data_rows", "nominal_data_cols", "occupied_patch_count", "nominal_occupied_data_area", "rectangular_footprint"))
        base_sizes[key] += value["occurrences"]
    (out / "base_shapes.json").write_text(json.dumps(base_list, indent=2) + "\n")
    write_csv(out / "base_sizes.csv", [dict(key, occurrences=count) for key, count in sorted(base_sizes.items())])
    write_csv(out / "base_sizes_by_benchmark.csv", bases_by_benchmark)
    # Also expose the core alone, so 'base size' can be distinguished from the
    # full decoder input with spatial buffers.
    commit_shapes = {}
    for value in global_shapes.values():
        patches = {(region[1], region[2]) for region in value["regions"] if region[0] == "commit"}
        row0, col0 = min(p[0] for p in patches), min(p[1] for p in patches)
        patches = sorted((row - row0, col - col0) for row, col in patches)
        key = json.dumps(patches, separators=(",", ":"))
        rows, cols = max(p[0] for p in patches) + 1, max(p[1] for p in patches) + 1
        distance = runs[0]["distance"]
        record = commit_shapes.setdefault(key, {
            "bbox_patch_rows": rows, "bbox_patch_cols": cols,
            "nominal_data_rows": distance * rows, "nominal_data_cols": distance * cols,
            "occupied_patch_count": len(patches), "occupied_patches": patches,
            "occurrences": 0,
        })
        record["occurrences"] += value["occurrences"]
    (out / "commit_base_shapes.json").write_text(json.dumps(list(commit_shapes.values()), indent=2) + "\n")
    commit_sizes = Counter()
    for record in commit_shapes.values():
        key = tuple((k, v) for k, v in record.items() if k not in ("occupied_patches", "occurrences"))
        commit_sizes[key] += record["occurrences"]
    write_csv(out / "commit_base_sizes.csv", [dict(key, occurrences=count) for key, count in sorted(commit_sizes.items())])
    return {"completed_runs": len(runs), "size_combinations": len(union),
            "region_shapes": len(global_shapes), "callback_volumes": volumes,
            "spatial_base_sizes": len(base_sizes), "spatial_base_shapes": len(base_list)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("window-inventory/d3"))
    parser.add_argument("--schedules", type=Path, default=Path("swiper-sim/benchmarks/cached_schedules"))
    parser.add_argument("--distance", type=int, default=3)
    parser.add_argument("--methods", nargs="+", choices=["sliding", "parallel", "aligned"], default=["sliding", "parallel", "aligned"])
    parser.add_argument("--benchmark", action="append", help="Relative schedule name without .lss; repeat to select")
    parser.add_argument("--decode-rounds", type=int, default=1)
    parser.add_argument("--decoders", type=int, default=None)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=0, help="Per-run wall timeout; 0 means unlimited")
    parser.add_argument("--round-cutoff", type=int, default=0)
    parser.add_argument("--workers", type=int, default=3,
                        help="Parallel workers for small schedules; large schedules run singly")
    parser.add_argument("--large-workers", type=int, default=1)
    parser.add_argument("--huge-workers", type=int, default=1)
    parser.add_argument("--summarize-only", action="store_true")
    args = parser.parse_args()
    if args.distance < 3 or args.distance % 2 == 0 or args.decode_rounds < 1:
        parser.error("distance must be odd and >=3; decode-rounds must be positive")
    if args.decoders is not None and args.decoders < 1:
        parser.error("decoders must be positive")
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "runs").mkdir(exist_ok=True)
    config = {"distance": args.distance, "decode_rounds": args.decode_rounds,
              "decoders": args.decoders, "seed": args.seed, "speculation_mode": None}
    config_path = args.out / "configuration.json"
    if config_path.exists() and json.loads(config_path.read_text()) != config:
        parser.error("output directory contains a different simulation configuration")
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    if not args.summarize_only:
        files = sorted(args.schedules.rglob("*.lss"), key=lambda p: p.stat().st_size)
        failures = []
        jobs = []
        for path in files:
            relative = path.relative_to(args.schedules).with_suffix("").as_posix()
            if args.benchmark and relative not in args.benchmark:
                continue
            for method in args.methods:
                destination = args.out / "runs" / (relative.replace("/", "__") + f"--{method}.json")
                if destination.exists():
                    continue
                jobs.append((path, relative, method, destination))
        # Limit concurrent memory usage for the largest compiled programs.
        categories = [(lambda size: size < 2_500_000, args.workers),
                      (lambda size: 2_500_000 <= size < 7_000_000, args.large_workers),
                      (lambda size: 7_000_000 <= size < 10_000_000, args.huge_workers),
                      (lambda size: size >= 10_000_000, 1)]
        for include, workers in categories:
            batch = [job for job in jobs if include(job[0].stat().st_size)]
            if not batch:
                continue
            with ProcessPoolExecutor(max_workers=workers) as pool:
                pending = {pool.submit(inventory, path, relative, method, args):
                           (relative, method, destination)
                           for path, relative, method, destination in batch}
                print(f"START batch: {len(batch)} runs, workers={workers}", flush=True)
                for future in as_completed(pending):
                    relative, method, destination = pending[future]
                    try:
                        result = future.result()
                        temporary = destination.with_suffix(".tmp")
                        temporary.write_text(json.dumps(result, indent=2) + "\n")
                        temporary.replace(destination)
                        print(f'DONE {relative} {method}: {result["window_count"]} windows, {len(result["sizes"])} sizes, {result["elapsed_seconds"]:.1f}s', flush=True)
                    except Exception:
                        error = {"benchmark": relative, "method": method, "error": traceback.format_exc()}
                        failures.append(error)
                        print(error["error"], flush=True)
                    summarize(args.out)
        (args.out / "failures.json").write_text(json.dumps(failures, indent=2) + "\n")
    print(json.dumps(summarize(args.out), indent=2), flush=True)
    spatial_report(args.out, args.schedules, args.methods, args.benchmark)
    if not args.summarize_only and failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
