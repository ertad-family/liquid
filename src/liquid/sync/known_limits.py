"""Proactive rate limit knowledge.

Bootstrap RateLimiter with known limits BEFORE first request,
so agents self-throttle from request #1.

Priority (highest confidence first):
  1. Response headers (observed, handled by RateLimiter)
  2. STATIC_KNOWN_LIMITS (curated from docs)
  3. CATEGORY_DEFAULTS (fallback by API category)
"""

from __future__ import annotations

from urllib.parse import urlparse

from liquid.models.schema import RateLimits

# Curated from documented API rate limits (verified <=2026-04).
# Format: hostname (or hostname prefix) -> RateLimits
STATIC_KNOWN_LIMITS: dict[str, RateLimits] = {
    # Payments
    "api.stripe.com": RateLimits(requests_per_second=100, burst=25),
    "api.paypal.com": RateLimits(requests_per_second=50),
    "api.adyen.com": RateLimits(requests_per_second=50),
    "api.square.com": RateLimits(requests_per_second=25),
    # E-commerce
    "api.shopify.com": RateLimits(requests_per_second=2, burst=40),
    "myshopify.com": RateLimits(requests_per_second=2, burst=40),
    "api.bigcommerce.com": RateLimits(requests_per_minute=450),
    # DevTools
    "api.github.com": RateLimits(requests_per_minute=83),  # 5000/hour authenticated
    "gitlab.com": RateLimits(requests_per_minute=600),
    "api.bitbucket.org": RateLimits(requests_per_hour=1000),
    "api.atlassian.com": RateLimits(requests_per_second=10),
    "api.linear.app": RateLimits(requests_per_hour=1500),
    # Messaging
    "slack.com": RateLimits(requests_per_minute=50),  # Tier 2 most common
    "api.twilio.com": RateLimits(requests_per_second=1),
    "api.sendgrid.com": RateLimits(requests_per_second=10),
    "api.mailgun.net": RateLimits(requests_per_second=5),
    "api.postmarkapp.com": RateLimits(requests_per_second=10),
    # Social
    "graph.facebook.com": RateLimits(requests_per_hour=200),
    "api.twitter.com": RateLimits(requests_per_minute=15),
    "api.linkedin.com": RateLimits(requests_per_minute=100),
    # CRM
    "api.hubapi.com": RateLimits(requests_per_second=10, burst=100),
    "salesforce.com": RateLimits(requests_per_day=100000),
    "api.pipedrive.com": RateLimits(requests_per_second=10),
    # Analytics
    "api.mixpanel.com": RateLimits(requests_per_second=60),
    "api.amplitude.com": RateLimits(requests_per_second=30),
    "segmentapi.com": RateLimits(requests_per_second=100),
    # Cloud
    "api.digitalocean.com": RateLimits(requests_per_hour=5000),
    "api.cloudflare.com": RateLimits(requests_per_minute=1200),
    "api.heroku.com": RateLimits(requests_per_hour=2400),
    # Storage
    "api.dropboxapi.com": RateLimits(requests_per_minute=120),
    "api.box.com": RateLimits(requests_per_minute=10000),
    # Productivity
    "api.notion.com": RateLimits(requests_per_second=3),
    "api.airtable.com": RateLimits(requests_per_second=5),
    "api.asana.com": RateLimits(requests_per_minute=150),
    "api.trello.com": RateLimits(requests_per_second=10, burst=100),
    "api.monday.com": RateLimits(requests_per_minute=600),
    # AI
    "api.openai.com": RateLimits(requests_per_minute=3500),
    "api.anthropic.com": RateLimits(requests_per_minute=1000),
    "generativelanguage.googleapis.com": RateLimits(requests_per_minute=1500),
    # Communication
    "api.discord.com": RateLimits(requests_per_second=50),  # global
    "api.telegram.org": RateLimits(requests_per_second=30),
    # Finance
    "api.plaid.com": RateLimits(requests_per_second=10),
    # Support
    "api.zendesk.com": RateLimits(requests_per_minute=700),
    "api.intercom.io": RateLimits(requests_per_minute=1000),
    # Analytics/Events
    "api.pagerduty.com": RateLimits(requests_per_second=900),
    # Music
    "api.spotify.com": RateLimits(requests_per_second=10),
    # Maps
    "maps.googleapis.com": RateLimits(requests_per_second=50),
    "api.mapbox.com": RateLimits(requests_per_minute=600),
}


# Conservative fallbacks by category (used when no hostname match).
CATEGORY_DEFAULTS: dict[str, RateLimits] = {
    "payments": RateLimits(requests_per_second=50),
    "ecommerce": RateLimits(requests_per_second=2),
    "messaging": RateLimits(requests_per_second=1),
    "social": RateLimits(requests_per_minute=100),
    "devtools": RateLimits(requests_per_minute=60),
    "crm": RateLimits(requests_per_second=5),
    "analytics": RateLimits(requests_per_second=30),
    "cloud": RateLimits(requests_per_minute=600),
    "storage": RateLimits(requests_per_minute=120),
    "ai": RateLimits(requests_per_minute=500),
    "maps": RateLimits(requests_per_second=10),
    "finance": RateLimits(requests_per_second=5),
    "security": RateLimits(requests_per_second=10),
    "hr": RateLimits(requests_per_minute=60),
    "project": RateLimits(requests_per_second=5),
    # Default fallback
    "other": RateLimits(requests_per_minute=60),
}


def lookup_known_limits(url: str) -> RateLimits | None:
    """Return curated rate limits for a known API hostname, or None."""
    parsed = urlparse(url)
    host = (parsed.hostname or url).lower()

    # Exact match
    if host in STATIC_KNOWN_LIMITS:
        return STATIC_KNOWN_LIMITS[host]

    # Suffix match (e.g. "myshop.myshopify.com" -> "myshopify.com")
    for known_host, limits in STATIC_KNOWN_LIMITS.items():
        if host.endswith("." + known_host) or host == known_host:
            return limits

    return None


def lookup_category_defaults(category: str | None) -> RateLimits:
    """Return conservative default rate limits for a category."""
    if not category:
        return CATEGORY_DEFAULTS["other"]
    return CATEGORY_DEFAULTS.get(category.lower(), CATEGORY_DEFAULTS["other"])


def infer_limits(url: str, category: str | None = None) -> RateLimits:
    """Best-effort proactive rate limit inference.

    Priority: hostname match > category default.
    """
    known = lookup_known_limits(url)
    if known is not None:
        return known
    return lookup_category_defaults(category)
