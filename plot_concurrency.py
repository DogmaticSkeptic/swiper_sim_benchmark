"""Plot SWIPER sweeps from saved CSV; missing runs never become zero idling."""
import argparse
import csv
import json
from pathlib import Path
import re

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import SymLogNorm
from matplotlib.backends.backend_pdf import PdfPages
import numpy as np

from sweep_concurrency import COUNTS, SPEEDS, ALPHAS, POLICIES

STATUS_LABELS = {'time_cutoff':'T', 'watchdog_cutoff':'T', 'round_cutoff':'R', 'backlog_cutoff':'B',
                 'memory_cutoff':'MEM', 'process_failed':'FAIL', 'error':'ERR', 'missing':'—'}


def read_rows(path):
    with Path(path).open() as f:
        rows=list(csv.DictReader(f))
    for r in rows:
        for k in ['M','speed','alpha','additional_idle_patch_rounds']:
            r[k]=float(r[k]) if r.get(k) else None
        r['baseline']=r['baseline']=='True'
    return rows


def grid(rows, algorithm, policy, which, counts=COUNTS, speeds=SPEEDS, alphas=ALPHAS):
    ys=speeds if which=='speed' else alphas
    values=np.full((len(ys),len(counts)),np.nan)
    statuses=np.full(values.shape,'missing',dtype=object)
    for r in rows:
        if r['baseline'] or r['algorithm']!=algorithm or r['policy']!=policy:
            continue
        if which=='speed' and r['alpha']!=0: continue
        if which=='alpha' and r['speed']!=1: continue
        y=r['speed'] if which=='speed' else r['alpha']
        if y not in ys or r['M'] not in counts: continue
        i,j=ys.index(y),counts.index(r['M'])
        if r['additional_idle_patch_rounds'] is not None:
            values[i,j]=r['additional_idle_patch_rounds']
            statuses[i,j]='ok'
        else:
            statuses[i,j]=r['status'] if r['status']!='ok' else 'baseline unavailable'
    return values,statuses


def normalization(values):
    finite=np.concatenate([v[np.isfinite(v)] for v in values])
    high=max(1,float(np.max(np.abs(finite)))) if len(finite) else 1
    negative=bool(len(finite) and min(finite)<0)
    return SymLogNorm(linthresh=1,linscale=1,vmin=-high if negative else 0,vmax=high), ('RdBu_r' if negative else 'cividis')


def short(x):
    if abs(x)<1000: return f'{x:.0f}'
    if abs(x)<1e6: return (f'{x/1e3:.1f}' if abs(x)<10000 else f'{x/1e3:.0f}')+'k'
    return f'{x/1e6:.1f}M'


def draw(ax, values, statuses, which, norm, cmap, counts=COUNTS, speeds=SPEEDS,
         alphas=ALPHAS, annotate=True):
    cm=plt.get_cmap(cmap).copy();cm.set_bad('#e8e8e8')
    im=ax.imshow(np.ma.masked_invalid(values),origin='lower',aspect='auto',norm=norm,cmap=cm)
    ys=speeds if which=='speed' else alphas
    ax.set_xticks(range(len(counts)),[str(m) for m in counts])
    ax.set_yticks(range(len(ys)),[f'{v:g}' for v in ys])
    ax.set_xlabel('Maximum parallel decodes, M')
    ax.set_ylabel('Isolated speed multiplier, s' if which=='speed' else 'Slowdown coefficient, α')
    ax.set_title('(a) α = 0' if which=='speed' else '(b) s = 1',loc='left',fontsize=9,pad=7)
    ax.set_xticks(np.arange(-.5,len(counts),1),minor=True)
    ax.set_yticks(np.arange(-.5,len(ys),1),minor=True)
    ax.grid(which='minor',color='white',linewidth=.35)
    ax.tick_params(which='minor',bottom=False,left=False)
    for (i,j),value in np.ndenumerate(values):
        if not np.isfinite(value):
            status=statuses[i,j]
            label='BASE' if status=='baseline unavailable' else STATUS_LABELS.get(status,'—')
            ax.text(j,i,label,ha='center',va='center',fontsize=5.5,color='#666666')
        elif annotate:
            rgba=cm(norm(value));luminance=.2126*rgba[0]+.7152*rgba[1]+.0722*rgba[2]
            ax.text(j,i,short(value),ha='center',va='center',fontsize=6.2,
                    color='white' if luminance<.48 else '#202020')
    for spine in ax.spines.values():spine.set_linewidth(.6)
    return im


def pair(rows,algorithm,policy,config):
    axis_values=(config.get('counts',COUNTS),config.get('speeds',SPEEDS),config.get('alphas',ALPHAS))
    data=[grid(rows,algorithm,policy,key,*axis_values) for key in ['speed','alpha']]
    norm,cmap=normalization([d[0] for d in data])
    fig,axes=plt.subplots(1,2,figsize=(8,3.35),layout='constrained')
    for ax,(values,statuses),key in zip(axes,data,['speed','alpha']):
        im=draw(ax,values,statuses,key,norm,cmap,*axis_values)
    bar=fig.colorbar(im,ax=axes,shrink=.9,pad=.025,fraction=.04)
    bar.set_label('Additional idle patch-rounds\nrelative to one-round baseline',fontsize=8)
    bar.ax.tick_params(labelsize=7)
    fig.suptitle(f'{algorithm} · {policy}\n'
                 f'CPU PyMatching surrogate · d = {config["distance"]} · QEC round = {config["round_us"]:g} µs · speculation off',
                 fontsize=9.5)
    return fig


def validate(rows, config=None):
    config = config or {}
    checks=[]
    counts=config.get('counts',COUNTS)
    speeds=config.get('speeds',SPEEDS)
    alphas=config.get('alphas',ALPHAS)
    axis_values=(counts,speeds,alphas)
    speed_one=speeds.index(1.)
    alpha_zero=alphas.index(0.)
    for a in sorted({r['algorithm'] for r in rows}):
        for p in config.get('policies',POLICIES):
            speed,_=grid(rows,a,p,'speed',*axis_values);alpha,_=grid(rows,a,p,'alpha',*axis_values)
            both=np.isfinite(speed[speed_one]) & np.isfinite(alpha[alpha_zero])
            if not np.array_equal(speed[speed_one,both],alpha[alpha_zero,both]):
                raise AssertionError((a,p,'overlap mismatch'))
            decoder_one=counts.index(1) if 1 in counts else None
            vals=alpha[:,decoder_one] if decoder_one is not None else np.array([])
            vals=vals[np.isfinite(vals)]
            if len(vals) and not np.all(vals==vals[0]):
                raise AssertionError((a,p,'M=1 differs with alpha'))
            checks.append(dict(algorithm=a,policy=p,overlap_comparisons=int(sum(both)),
                               single_decoder_comparisons=len(vals),
                               negative_cells=int(sum(np.sum(v<0) for v in [speed,alpha]))))
    return checks


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    args=parser.parse_args();out=args.directory
    rows=read_rows(out/'sweep.csv');config=json.loads((out/'configuration.json').read_text())
    config.setdefault('counts', COUNTS)
    config.setdefault('speeds', SPEEDS)
    config.setdefault('alphas', ALPHAS)
    config.setdefault('policies', POLICIES)
    checks=validate(rows,config)
    (out/'acceptance.json').write_text(json.dumps(checks,indent=2)+'\n')
    figures=out/'figures';figures.mkdir(exist_ok=True)
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':8,'axes.labelsize':8,
                         'xtick.labelsize':8,'ytick.labelsize':8,'svg.fonttype':'none',
                         'pdf.fonttype':42,'savefig.dpi':180})
    algorithms=sorted({r['algorithm'] for r in rows})
    with PdfPages(figures/'all_benchmarks.pdf') as pdf:
        for algorithm in algorithms:
            for policy in config['policies']:
                fig=pair(rows,algorithm,policy,config)
                name=re.sub(r'[^a-zA-Z0-9_.-]+','__',algorithm)+'__'+policy
                for ext in ['png','svg','pdf']:fig.savefig(figures/f'{name}.{ext}')
                pdf.savefig(fig)
                plt.close(fig)
    # Atlas pages share a global scale across every algorithm and policy.
    axis_values=(config['counts'],config['speeds'],config['alphas'])
    all_values=[grid(rows,a,p,k,*axis_values)[0] for a in algorithms for p in config['policies'] for k in ['speed','alpha']]
    norm,cmap=normalization(all_values)
    with PdfPages(figures/'comparison_atlas.pdf') as pdf:
        for policy in config['policies']:
            for page,start in enumerate(range(0,len(algorithms),9),1):
                subset=algorithms[start:start+9]
                fig,axes=plt.subplots(len(subset),2,figsize=(8.3,2*len(subset)+.5),layout='constrained',squeeze=False)
                for i,a in enumerate(subset):
                    for j,key in enumerate(['speed','alpha']):
                        values,statuses=grid(rows,a,policy,key,*axis_values)
                        im=draw(axes[i,j],values,statuses,key,norm,cmap,*axis_values,annotate=False)
                        axes[i,j].set_title(f'{a} | '+('α = 0' if key=='speed' else 's = 1'),loc='left',fontsize=8)
                bar=fig.colorbar(im,ax=axes,pad=.02,fraction=.025,shrink=.5)
                bar.set_label('Additional idle patch-rounds relative to one-round baseline')
                fig.suptitle(f'{policy} · shared scale across all benchmarks\n'
                             f'CPU PyMatching surrogate · d = {config["distance"]} · QEC round = {config["round_us"]:g} µs · speculation off',
                             fontsize=9.5)
                fig.savefig(figures/f'atlas__{policy}__{page}.png')
                pdf.savefig(fig);plt.close(fig)
    lines=['# Concurrency heatmaps','',
        f'Distance {config["distance"]}; QPU round duration {config["round_us"]:g} µs; simulator and hash seed {config["seed"]}; speculation off; communication overhead zero.','',
        'The reference is CPU PyMatching timing on representative complete surgery circuits. It is a scheduling surrogate, not a GPU/FPGA calibration or exact interior-window timing.','',
        'Left: decoder capacity versus isolated speed, with α = 0. Right: capacity versus dynamic slowdown α, with s = 1. Every active decode receives 1 / [1 + α(k − 1)] isolated work per round at actual pre-progress occupancy k. Speed is applied to unrounded microseconds. Completion is observed at round boundaries.','',
        'Colors show additional idle patch-rounds relative to the same schedule/policy baseline with unlimited slots, α = 0 and one-round decoding. Idle patch-rounds sum is_unwanted_idle over the generated syndrome trace, so this counts patches rather than elapsed rounds. Signed differences are retained.','',
        'Each pair shares a symmetric-log color scale with a linear region through zero. The comparison atlas shares one scale across all benchmarks. Numbers in cells are rounded for display; sweep.csv retains exact integer counts. No statistical uncertainty is implied by this fixed-seed deterministic sweep.','',
        'Gray cells are missing: T = time cutoff, R = round cutoff, B = backlog cutoff, MEM = memory cutoff, FAIL = process failure, ERR = simulation error, BASE = baseline unavailable. They are never rendered as zero. Full status and cutoffs are recorded in the CSV and cell JSON files.','',
        '`all_benchmarks.pdf` contains one pair per algorithm and policy. `comparison_atlas.pdf` compares all algorithms. Individual pairs are exported as PNG, editable SVG and PDF.','',
        '| Algorithm | '+ ' | '.join(p.title() for p in config['policies']) +' |',
        '|'+'|'.join(['---']*(1+len(config['policies'])))+'|']
    for a in algorithms:
        name=re.sub(r'[^a-zA-Z0-9_.-]+','__',a)
        lines.append('| '+a+' | '+' | '.join(f'[PNG](figures/{name}__{p}.png)' for p in config['policies'])+' |')
    (out/'README.md').write_text('\n'.join(lines)+'\n')
    print(f'Wrote {len(algorithms)*len(config["policies"])} heatmap pairs and comparison atlas')


if __name__=='__main__':main()
