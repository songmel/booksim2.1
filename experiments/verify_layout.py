#!/usr/bin/env python3
"""Verify the preserved upstream baseline and collective-free optimized core."""
import json

from repro_common import ROOT, WORKSPACE, core_hashes


def verify():
    expected = json.loads((ROOT / "core_manifest.json").read_text())
    for name in ("booksim2", "booksim-opt"):
        actual = core_hashes(WORKSPACE / name)
        target = expected["repositories"][name]["source_sha256"]
        changed = sorted(p for p in set(actual) | set(target) if actual.get(p) != target.get(p))
        if changed:
            raise RuntimeError(f"Core source differs from the reproduction inventory: {name}: {changed}")
        for p in (WORKSPACE / name / "src").rglob("*"):
            if p.is_file() and p.suffix in (".cpp", ".hpp") and "allreduce" in p.read_text().lower():
                raise RuntimeError(f"Collective-specific code remains in core: {p}")
    print("PASS core inventories: upstream baseline and OPT1..OPT6-only core", flush=True)
    return True


if __name__ == "__main__":
    verify()
