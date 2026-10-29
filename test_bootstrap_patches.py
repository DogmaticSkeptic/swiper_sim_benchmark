"""Bootstrap must apply exactly once and preserve unrelated user changes."""
from pathlib import Path
import subprocess

import pytest
import bootstrap_sources as bootstrap


def git(path, *args):
    return subprocess.check_output(['git',*args],cwd=path,text=True).strip()


def test_patch_application_idempotence_and_local_edit_refusal(tmp_path, monkeypatch):
    repo=tmp_path/'upstream';repo.mkdir()
    git(repo,'init','-q')
    git(repo,'config','commit.gpgsign','false')
    git(repo,'config','user.name','Patch Test')
    git(repo,'config','user.email','test@example.invalid')
    source=repo/'source.py';source.write_text('a = 1\n')
    git(repo,'add','source.py');git(repo,'commit','-qm','base')
    source.write_text('a = 2\n')
    patch=tmp_path/'change.patch';patch.write_text(git(repo,'diff','--binary')+'\n')
    git(repo,'checkout','--','source.py')
    monkeypatch.setattr(bootstrap,'ROOT',tmp_path)
    bootstrap.apply_patches(repo,['change.patch'])
    assert source.read_text()=='a = 2\n'
    bootstrap.apply_patches(repo,['change.patch'])
    source.write_text('a = 3\n')
    with pytest.raises(RuntimeError,match='local edits'):
        bootstrap.apply_patches(repo,['change.patch'])
    assert source.read_text()=='a = 3\n'
    source.write_text('a = 2\n')
    untracked=repo/'notes.txt';untracked.write_text('preserve me')
    with pytest.raises(RuntimeError):
        bootstrap.apply_patches(repo,['change.patch'])
    assert untracked.read_text()=='preserve me'
    untracked.unlink()
    git(repo,'add','source.py')
    bootstrap.apply_patches(repo,['change.patch'])
    assert git(repo,'diff','--cached')
