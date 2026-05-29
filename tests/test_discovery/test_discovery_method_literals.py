"""Guard against the 0.55-0.59 SSE class of bug: a discovery strategy that
emits a `discovery_method` not in `APISchema`'s allowed Literal raises a
ValidationError that the pipeline swallows (`except Exception: return None`),
so the whole strategy silently never works. This statically cross-checks every
`discovery_method="X"` used under liquid.discovery against the model's Literal."""

from __future__ import annotations

import re
import typing
from pathlib import Path

import liquid.discovery
from liquid.models.schema import APISchema


def _allowed_methods() -> set[str]:
    ann = APISchema.model_fields["discovery_method"].annotation
    return set(typing.get_args(ann))


def _used_methods() -> dict[str, list[str]]:
    """Map each discovery_method string literal to the files that emit it."""
    pkg_dir = Path(liquid.discovery.__file__).parent
    pattern = re.compile(r'discovery_method\s*=\s*"([a-z0-9_]+)"')
    used: dict[str, list[str]] = {}
    for path in pkg_dir.glob("*.py"):
        for method in pattern.findall(path.read_text("utf-8")):
            used.setdefault(method, []).append(path.name)
    return used


def test_every_emitted_discovery_method_is_an_allowed_literal():
    allowed = _allowed_methods()
    used = _used_methods()
    assert used, "no discovery_method= literals found — scan is broken"
    offenders = {m: files for m, files in used.items() if m not in allowed}
    assert not offenders, (
        f"discovery_method(s) not in APISchema Literal {sorted(allowed)}: {offenders} "
        "— these would raise ValidationError and be silently swallowed to None."
    )


def test_sse_is_an_allowed_literal():
    # Direct regression assertion for the specific bug fixed in 0.59.1.
    assert "sse" in _allowed_methods()
