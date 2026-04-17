"""Intent layer — canonical operations that speak agent language."""

from liquid.intent.models import Intent, IntentConfig
from liquid.intent.registry import CANONICAL_INTENTS, get_intent, list_intents

__all__ = [
    "CANONICAL_INTENTS",
    "Intent",
    "IntentConfig",
    "get_intent",
    "list_intents",
]
