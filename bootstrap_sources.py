"""Fetch pinned external sources without vendoring them into the parent repo."""

import argparse
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          stdout=subprocess.PIPE, text=True).stdout.strip()


def apply_patches(path, patches):
    """Accept a pristine tree or exactly the tracked combined diff.

    Comparing against HEAD includes staged and unstaged changes, and avoids
    materializing large blobs excluded by upstream's sparse checkout.
    """
    patches = [ROOT / patch for patch in patches]
    status = git("status", "--porcelain", cwd=path)
    if not patches:
        if status:
            raise RuntimeError(f"{path.name} has unrelated local changes; refusing to overwrite")
        return
    expected = "\n".join(p.read_text().strip() for p in patches)
    actual = git("diff", "--binary", "HEAD", "--", cwd=path)
    untracked = git("ls-files", "--others", "--exclude-standard", cwd=path)
    if actual == expected and not untracked:
        print(f"Verified patches already applied to {path.name}")
        return
    if status:
        raise RuntimeError(f"{path.name} has unrelated or partial local edits; refusing to overwrite")
    patch_args = [str(p) for p in patches]
    subprocess.run(["git", "apply", "--check", *patch_args], cwd=path, check=True)
    subprocess.run(["git", "apply", *patch_args], cwd=path, check=True)
    print(f"Applied {len(patches)} tracked patch(es) to {path.name}")


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
            apply_patches(path, spec.get("patches", []))
            print(f"Verified {name} at {head}")
            continue
        git("clone", "--no-checkout", "--depth=1", "--filter=blob:none", spec["url"], str(path))
        git("fetch", "--depth=1", "origin", spec["commit"], cwd=path)
        if spec.get("sparse_paths"):
            git("sparse-checkout", "init", "--cone", cwd=path)
            git("sparse-checkout", "set", *spec["sparse_paths"], cwd=path)
        git("checkout", "--detach", spec["commit"], cwd=path)
        apply_patches(path, spec.get("patches", []))
        print(f"Fetched {name} at {spec['commit']}")


if __name__ == "__main__":
    main()
