"""Render the spatial footprint masks from an inventory as standalone figures."""

import argparse
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path, nargs="?", default=Path("window-inventory/d3"))
    args = parser.parse_args()
    shapes = sorted(json.loads((args.inventory / "base_shapes.json").read_text()),
                    key=lambda s: (s["nominal_data_rows"], s["nominal_data_cols"],
                                   s["occupied_patch_count"], s["occupied_patches"]))
    columns = 5
    rows = math.ceil(len(shapes) / columns)
    figure, axes = plt.subplots(rows, columns, figsize=(13, 2.6 * rows), squeeze=False)
    for index, axis in enumerate(axes.flat):
        axis.set_axis_off()
        if index >= len(shapes):
            continue
        shape = shapes[index]
        height, width = shape["bbox_patch_rows"], shape["bbox_patch_cols"]
        for row in range(height):
            for col in range(width):
                axis.add_patch(Rectangle((col, height - 1 - row), 1, 1,
                                         facecolor="#edf1f5", edgecolor="white", linewidth=2))
        for row, col in shape["occupied_patches"]:
            axis.add_patch(Rectangle((col, height - 1 - row), 1, 1,
                                     facecolor="#217a82", edgecolor="white", linewidth=2))
        axis.set_xlim(-0.2, width + 0.2)
        axis.set_ylim(-0.2, height + 0.2)
        axis.set_aspect("equal")
        axis.set_title(f'{shape["nominal_data_rows"]}×{shape["nominal_data_cols"]}  ·  '
                       f'{shape["occupied_patch_count"]} '
                       f'{"patch" if shape["occupied_patch_count"] == 1 else "patches"}\n'
                       f'{shape["base_shape_id"][:8]}', fontsize=10, color="#213547")
    figure.suptitle("Distance 3: observed decoding-window footprints", fontsize=17, y=0.995)
    figure.text(0.5, 0.015,
                "Each colored square is one nominal 3×3 patch. Commit and buffer patches are combined; temporal extent is ignored.\n"
                "Gray squares are outside the footprint. A spatial mask alone does not specify the decoder graph.",
                ha="center", fontsize=10, color="#364b5b")
    figure.tight_layout(rect=(0, 0.065, 1, 0.96))
    for extension in ("svg", "png"):
        figure.savefig(args.inventory / f"base_shapes.{extension}", dpi=160, bbox_inches="tight")
    plt.close(figure)


if __name__ == "__main__":
    main()
