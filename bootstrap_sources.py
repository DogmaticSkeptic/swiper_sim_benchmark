"""Fetch pinned external sources without vendoring them into the parent repo."""

import argparse
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          stdout=subprocess.PIPE, text=True).stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=["swiper-sim", "tqec-source"])
    args = parser.parse_args()
    sources = json.loads((ROOT / "upstream-sources.json").read_text())
    for name, spec in sources.items():
        if args.only and name != args.only:
            continue
        path = ROOT / name
        if path.exists():
            if not (path / ".git").exists():
                raise RuntimeError(f"{name} exists but is not a Git checkout; preserve it and resolve manually")
            head = git("rev-parse", "HEAD", cwd=path)
            origin = git("remote", "get-url", "origin", cwd=path)
            if head != spec["commit"] or origin != spec["url"]:
                raise RuntimeError(f"{name} differs from the pinned source; existing checkouts are never reset")
            if git("status", "--porcelain", cwd=path):
                raise RuntimeError(f"{name} has local changes; existing checkouts are never overwritten")
            print(f"Verified {name} at {head}")
            continue
        git("clone", "--no-checkout", "--depth=1", "--filter=blob:none", spec["url"], str(path))
        git("fetch", "--depth=1", "origin", spec["commit"], cwd=path)
        if spec.get("sparse_paths"):
            git("sparse-checkout", "init", "--cone", cwd=path)
            git("sparse-checkout", "set", *spec["sparse_paths"], cwd=path)
        git("checkout", "--detach", spec["commit"], cwd=path)
        print(f"Fetched {name} at {spec['commit']}")


if __name__ == "__main__":
    main()
