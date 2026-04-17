"""Intent executor — translates canonical input into adapter-specific calls."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from liquid.action.builder import RequestBodyBuilder

if TYPE_CHECKING:
    from liquid.intent.models import IntentConfig
    from liquid.models.action import ActionConfig
    from liquid.models.adapter import AdapterConfig

logger = logging.getLogger(__name__)


def resolve_intent(
    config: AdapterConfig,
    intent_name: str,
) -> IntentConfig | None:
    """Find an IntentConfig matching the given intent name."""
    for ic in config.intents:
        if ic.intent_name == intent_name:
            return ic
    return None


def compile_to_action_data(
    intent_config: IntentConfig,
    canonical_data: dict[str, Any],
) -> dict[str, Any]:
    """Translate canonical intent input into adapter's action input using mappings.

    Returns raw fields suitable for passing as ``data`` to :meth:`Liquid.execute`.
    """
    # Reuse RequestBodyBuilder — it applies mappings (source_field → target_path)
    # In our case source_field == canonical field name.
    builder = RequestBodyBuilder(
        mappings=intent_config.field_mappings,
        static_values=intent_config.static_values,
    )
    return builder.build(canonical_data)


def find_action_for_intent(
    config: AdapterConfig,
    intent_config: IntentConfig,
) -> ActionConfig | None:
    """Find the ActionConfig this intent is bound to."""
    if not intent_config.action_id:
        return None
    return next(
        (a for a in config.actions if a.action_id == intent_config.action_id),
        None,
    )
