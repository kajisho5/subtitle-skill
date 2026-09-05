"""Deterministic identity, provenance and reuse support.

Identity is computed only from semantically meaningful inputs (operation,
parameters, subtitle content, resolved input identities, skill/contract
version). Timestamps, temp paths and process ids are never included, so
identical requests against an unchanged skill version produce an
identical identity and therefore a stable, reusable output.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_identity(*, skill_version: str, contract_version: str, operation: str, payload: Mapping[str, Any]) -> str:
    """`payload` must contain only deterministic, semantically relevant
    fields (subtitle content, format, parameters, input file hashes) --
    never timestamps or filesystem-specific temp paths.
    """
    material = {
        "skill_version": skill_version,
        "contract_version": contract_version,
        "operation": operation,
        "payload": payload,
    }
    return hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()
