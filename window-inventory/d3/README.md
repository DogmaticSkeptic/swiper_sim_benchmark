# Distance-3 spatial base inventory

Coverage: 132/132 runs across 44 supplied schedule files and 3 window policies.

Configuration: d=3, one-round decoding, unlimited decoder slots, no speculation, seed 0.

Commit-only bounding boxes (without buffers): 3×3, 3×6, 3×9, 6×3, 6×6, 9×3.

Every basic tile is 3×3 in the nominal data-grid model. The dimensions below are bounding boxes of all commit and buffer patches in a decoding window, with the temporal extent ignored. They are not physical data-plus-ancilla dimensions. A bounding box may contain unoccupied tiles; the exact patch masks are in `base_shapes.json`. A projected footprint may combine patches from different rounds.

Found **10 bounding-box dimensions** and **19 distinct spatial masks**.

| Nominal bounding box | Observed occupied-patch counts | Spatial masks | Filled rectangle observed |
| --- | --- | --- | --- |
| 3×3 | 1 | 1 | yes |
| 3×6 | 2 | 1 | yes |
| 3×9 | 3 | 1 | yes |
| 6×3 | 2 | 1 | yes |
| 6×6 | 3 4 | 5 | yes |
| 6×9 | 4 | 2 | no |
| 9×3 | 3 | 1 | yes |
| 9×6 | 4 5 | 3 | no |
| 9×9 | 5 7 | 3 | no |
| 9×12 | 6 | 1 | no |

## Per benchmark: union of sliding, parallel, and aligned policies

| Benchmark schedule | Spatial bounding boxes |
| --- | --- |
| H2 | 3×3, 3×6, 3×9, 6×3, 6×6, 9×3 |
| adder_n18 | 3×3, 3×6, 3×9, 6×3, 6×6, 9×3 |
| carleman_2_4 | 3×3, 3×6, 3×9, 6×3, 6×6, 9×3 |
| fermi_hubbard_2_2_Kagome | 3×3, 3×6, 3×9, 6×3, 6×6, 9×3 |
| fermi_hubbard_2_2_Square | 3×3, 3×6, 3×9, 6×3, 6×6, 9×3 |
| fermi_hubbard_4_4_Square | 3×3, 3×6, 3×9, 6×3, 6×6, 9×3 |
| grover_ancilla_5 | 3×3, 3×6, 3×9, 6×3, 6×6, 9×3 |
| heisenberg_3 | 3×3, 3×6, 3×9, 6×3, 6×6, 9×3 |
| memory_10000 | 3×3 |
| msd_15to1 | 3×3, 3×6, 3×9, 6×3, 6×6, 6×9, 9×3, 9×6, 9×9, 9×12 |
| qft_10 | 3×3, 3×6, 3×9, 6×3, 6×6, 9×3 |
| qpeexact_10 | 3×3, 3×6, 3×9, 6×3, 6×6, 9×3 |
| qpeexact_5 | 3×3, 3×6, 3×9, 6×3, 6×6, 9×3 |
| qrom_15_15 | 3×3, 3×6, 3×9, 6×3, 6×6, 9×3 |
| random_t/random_t_1000_200_0 | 3×3, 3×6, 6×3 |
| random_t/random_t_1000_200_1 | 3×3, 3×6, 6×3 |
| random_t/random_t_1000_200_10 | 3×3, 3×6, 6×3 |
| random_t/random_t_1000_200_11 | 3×3, 3×6, 6×3 |
| random_t/random_t_1000_200_12 | 3×3, 3×6, 6×3 |
| random_t/random_t_1000_200_13 | 3×3, 3×6, 6×3 |
| random_t/random_t_1000_200_14 | 3×3, 3×6, 6×3 |
| random_t/random_t_1000_200_15 | 3×3, 3×6, 6×3 |
| random_t/random_t_1000_200_16 | 3×3, 3×6, 6×3 |
| random_t/random_t_1000_200_17 | 3×3, 3×6, 6×3 |
| random_t/random_t_1000_200_18 | 3×3, 3×6, 6×3 |
| random_t/random_t_1000_200_19 | 3×3, 3×6, 6×3 |
| random_t/random_t_1000_200_2 | 3×3, 3×6, 6×3 |
| random_t/random_t_1000_200_3 | 3×3, 3×6, 6×3 |
| random_t/random_t_1000_200_4 | 3×3, 3×6, 6×3 |
| random_t/random_t_1000_200_5 | 3×3, 3×6, 6×3 |
| random_t/random_t_1000_200_6 | 3×3, 3×6, 6×3 |
| random_t/random_t_1000_200_7 | 3×3, 3×6, 6×3 |
| random_t/random_t_1000_200_8 | 3×3, 3×6, 6×3 |
| random_t/random_t_1000_200_9 | 3×3, 3×6, 6×3 |
| random_t_10000_200_0 | 3×3, 3×6, 6×3 |
| random_t_1000_200_0 | 3×3, 3×6, 6×3 |
| regular_t_1000_0 | 3×3, 3×6, 6×3 |
| rz_1e-05 | 3×3, 6×3, 9×3 |
| rz_1e-10 | 3×3, 6×3, 9×3 |
| rz_1e-15 | 3×3, 6×3, 9×3 |
| rz_1e-20 | 3×3, 6×3, 9×3 |
| rz_seq_100_1e-10 | 3×3, 6×3, 9×3 |
| rz_seq_10_1e-10 | 3×3, 6×3, 9×3 |
| toffoli | 3×3, 3×6, 3×9, 6×3, 6×6 |

## Files

* `base_bounding_boxes.csv`: compact list of spatial dimensions, ignoring rounds.
* `base_sizes.csv`: dimensions plus occupied-patch count and occurrence count.
* `base_sizes_by_benchmark.csv`: spatial sizes separated by algorithm and policy.
* `base_shapes.json`: exact normalized occupied-patch coordinates for each mask.
* `commit_base_sizes.csv` and `commit_base_shapes.json`: core spatial footprints without buffers.
* `coverage.json`: expected/completed runs and any missing runs.
* `runs/*.json`: source hashes, full window-region geometry, and per-run results.

Only translation is normalized: rotations and reflections are kept distinct. Counts count constructed windows across the supplied files and policies, including the duplicate random-T source file. This is a complete inventory for these reference runs, not all possible latencies, pools, seeds, or speculation outcomes. Use the region-level geometry and surgery protocol to construct decoder circuits; a spatial bounding box alone does not specify a decoding graph.
