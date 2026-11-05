#!/usr/bin/env python3
"""Verify that both simulator working trees remain byte-for-byte unchanged."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parents[1]


def verify():
    before = json.loads((ROOT / "source_state_before.json").read_text())
    for repo, expected in before.items():
        current = {str(p.relative_to(WORKSPACE / repo)): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in sorted((WORKSPACE / repo / "src").rglob("*"))
                   if p.is_file() and p.suffix in (".cpp", ".hpp", ".l", ".y")}
        if current != expected["working_sources"]:
            changed = [p for p in set(current) | set(expected["working_sources"])
                       if current.get(p) != expected["working_sources"].get(p)]
            raise RuntimeError(f"Simulator sources changed since experiment setup: {repo}: {changed}")
        if hashlib.sha256((WORKSPACE / repo / "src/Makefile").read_bytes()).hexdigest() != expected["makefile_sha256"]:
            raise RuntimeError(f"Original Makefile changed: {repo}")
    print("Both simulator working trees and Makefiles are unchanged.")
    return True


if __name__ == "__main__":
    verify()
