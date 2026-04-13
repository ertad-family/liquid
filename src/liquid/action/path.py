"""Path parameter resolution for write endpoints."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from liquid.models.schema import Parameter, ParameterLocation

_PARAM_RE = re.compile(r"\{(\w+)\}")


class PathResolver:
    """Resolves path parameters from agent data."""

    def resolve(
        self,
        path_template: str,
        data: dict[str, Any],
        parameters: list[Parameter] | None = None,
    ) -> str:
        """Replace {param} placeholders in path with values from data.

        Resolution priority:
        1. Explicit fields in `data` matching parameter name
        2. PATH parameters from endpoint definition
        """
        params = parameters or []
        path_param_names = {p.name for p in params if p.location == ParameterLocation.PATH}

        def _replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name in data:
                return quote(str(data[name]), safe="")
            if name in path_param_names:
                msg = f"Path parameter '{name}' declared but not provided in data"
                raise ValueError(msg)
            msg = f"Unresolved path parameter: '{name}'"
            raise ValueError(msg)

        return _PARAM_RE.sub(_replace, path_template)
