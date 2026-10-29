"""Preserve signed idling, real zeroes, and unavailable runs in heatmaps."""
import numpy as np
from plot_concurrency import grid, normalization, validate
from sweep_concurrency import positive
import argparse
import pytest


def row(**kwargs):
    return dict(algorithm='a',policy='aligned',baseline=False,M=1,speed=1.,alpha=0.,
                status='ok',additional_idle_patch_rounds=0,**kwargs)


def test_signed_zero_and_failed_cells_remain_distinct():
    rows=[row(),dict(row(),M=2,additional_idle_patch_rounds=-3),
          dict(row(),M=4,status='backlog_cutoff',additional_idle_patch_rounds=None)]
    values,statuses=grid(rows,'a','aligned','speed')
    assert values[2,0]==0
    assert values[2,1]==-3
    assert np.isnan(values[2,2]) and statuses[2,2]=='backlog_cutoff'
    norm,cmap=normalization([values])
    assert norm.vmin<=-3 and norm.vmax>=0 and np.isfinite(norm(0))
    assert cmap=='RdBu_r'
    validate(rows)


@pytest.mark.parametrize('value',['nan','inf','-1','0'])
def test_speed_and_round_parameters_must_be_positive_finite(value):
    with pytest.raises(argparse.ArgumentTypeError):positive(value)
