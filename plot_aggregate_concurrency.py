"""Plot mean idle-round heatmaps aggregated across available benchmarks."""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

from plot_concurrency import COUNTS, SPEEDS, ALPHAS, grid, normalization, read_rows


def aggregate(rows, algorithms, policy, which, counts, speeds, alphas):
    grids = [grid(rows, algorithm, policy, which, counts, speeds, alphas)[0]
             for algorithm in algorithms]
    stacked = np.stack(grids)
    coverage = np.isfinite(stacked).sum(axis=0)
    sums = np.nansum(stacked, axis=0)
    means = np.divide(sums, coverage, out=np.full(sums.shape, np.nan), where=coverage > 0)
    return means, coverage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', nargs='?', type=Path, default=Path('results/concurrency'))
    args = parser.parse_args()
    out = args.directory
    rows = read_rows(out / 'sweep.csv')
    config = json.loads((out / 'configuration.json').read_text())
    counts = config.get('counts', COUNTS)
    speeds = config.get('speeds', SPEEDS)
    alphas = config.get('alphas', ALPHAS)
    algorithms = sorted({row['algorithm'] for row in rows})
    policies = config['policies']
    datasets = {(policy, kind): aggregate(rows, algorithms, policy, kind,
                                          counts, speeds, alphas)
                for policy in policies for kind in ('speed', 'alpha')}
    norm, cmap = normalization([means for means, _ in datasets.values()])

    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 8,
                         'axes.labelsize': 8, 'xtick.labelsize': 8,
                         'ytick.labelsize': 8, 'svg.fonttype': 'none',
                         'pdf.fonttype': 42, 'savefig.dpi': 180})
    fig, axes = plt.subplots(len(policies), 2, figsize=(9, 2.45 * len(policies) + .75),
                             layout='constrained', squeeze=False)
    cm = plt.get_cmap(cmap).copy()
    cm.set_bad('#e8e8e8')
    for row_index, policy in enumerate(policies):
        for column, (kind, y_values) in enumerate((('speed', speeds), ('alpha', alphas))):
            means, coverage = datasets[(policy, kind)]
            ax = axes[row_index, column]
            im = ax.imshow(np.ma.masked_invalid(means), origin='lower', aspect='auto',
                           norm=norm, cmap=cm)
            ax.set_xticks(range(len(counts)), [str(count) for count in counts])
            ax.set_yticks(range(len(y_values)), [f'{value:g}' for value in y_values])
            ax.set_xlabel('Maximum parallel decodes, M')
            ax.set_ylabel('Isolated speed multiplier, s' if kind == 'speed'
                          else 'Slowdown coefficient, α')
            subtitle = 'α = 0' if kind == 'speed' else 's = 1'
            ax.set_title(f'{policy} · {subtitle}', loc='left', fontsize=9)
            ax.set_xticks(np.arange(-.5, len(counts), 1), minor=True)
            ax.set_yticks(np.arange(-.5, len(y_values), 1), minor=True)
            ax.grid(which='minor', color='white', linewidth=.35)
            ax.tick_params(which='minor', bottom=False, left=False)
            for (i, j), value in np.ndenumerate(means):
                n = int(coverage[i, j])
                if n == 0:
                    label = '—\n0 valid'
                    color = '#666666'
                else:
                    label = f'{value:,.0f}\n{n}/{len(algorithms)} valid'
                    rgba = cm(norm(value))
                    luminance = .2126 * rgba[0] + .7152 * rgba[1] + .0722 * rgba[2]
                    color = 'white' if luminance < .48 else '#202020'
                ax.text(j, i, label, ha='center', va='center', fontsize=5.5, color=color)

    bar = fig.colorbar(im, ax=axes, shrink=.88, pad=.02, fraction=.025)
    bar.set_label('Mean additional idle patch-rounds per benchmark\nrelative to one-round baseline')
    valid_counts = [int(coverage.max()) for _, coverage in datasets.values()]
    fig.suptitle(f'Mean across {len(algorithms)} benchmarks · CPU PyMatching timing surrogate\n'
                 f'd = {config["distance"]} · QEC round = {config["round_us"]:g} µs · '
                 'speculation off · cells show valid benchmark coverage', fontsize=10)
    fig.text(.5, -.005,
             f'Each cell averages successful runs only; cutoffs and failures are omitted from the mean. '
             f'Maximum valid coverage: {max(valid_counts)}/{len(algorithms)} benchmarks.',
             ha='center', fontsize=7)
    for extension in ('png', 'svg', 'pdf'):
        fig.savefig(out / f'average_benchmarks.{extension}', bbox_inches='tight')
    plt.close(fig)

    print(f'Wrote aggregate heatmap for {len(algorithms)} benchmarks to {out}')
    for policy in policies:
        for kind in ('speed', 'alpha'):
            _, coverage = datasets[(policy, kind)]
            print(f'{policy} {kind}: cell coverage {coverage.min()}–{coverage.max()}/{len(algorithms)}')


if __name__ == '__main__':
    main()
