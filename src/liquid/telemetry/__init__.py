"""Opt-in telemetry for crowdsourced rate limit aggregation.

When `Liquid(contribute_telemetry=True)`:
- Observed rate limit headers are batched
- Periodically flushed to Liquid Cloud hub
- Never sends credentials, user IDs, or request/response bodies
- Only hostname + standard headers + timestamps
"""

from liquid.telemetry.anonymize import anonymize_event, extract_hostname
from liquid.telemetry.collector import TelemetryCollector

__all__ = ["TelemetryCollector", "anonymize_event", "extract_hostname"]
