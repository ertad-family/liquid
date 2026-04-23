"""Cross-API canonical data shapes — 0.25.0 normalizer breadth.

Each shape is research-backed against the top APIs where that value appears.
Shapes preserve ``original`` verbatim (same discipline as
:class:`~liquid.normalize.money.Money`) so lossless round-trips remain
possible when callers need provider-specific fields.

Ship order (recurrence x divergence score):
  1. :class:`PostalAddress` — 10/10 providers, high divergence.
  2. :class:`Phone` — 9/10, enforces E.164-ish canonical string.
  3. :class:`Email` — 8/10, surfaces ``verified``/``primary``/``domain``.
  4. :class:`PersonName` — 9/10, ``full``/``given``/``family`` only (no
     middle/prefix/suffix — too sparse to round-trip).
  5. :class:`FileAttachment` — 8/10 (url/filename/mime/size/sha256).
  6. :class:`UserRef` — 9/10 ("who did this" attribution).
  7. :class:`Tag` — 7/10 (auto-splits comma-separated strings).
  8. :class:`GeoPoint` — 5/10 but lat/lng-vs-GeoJSON ambiguity causes
     real bugs, so worth canonicalising.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

# ─────────────────────────── PostalAddress ───────────────────────────


class PostalAddress(BaseModel):
    """Canonical postal address.

    Maps ``state``/``province``/``region``/``admin_area_1`` → ``region``.
    Country codes are normalised to ISO-3166 alpha-2 (upper case) via
    :func:`_coerce_country_code` when a 2-letter string is supplied;
    longer values (``"United States"``) pass through for the adapter to
    resolve.
    """

    line1: str | None = None
    line2: str | None = None
    city: str | None = None
    region: str | None = None
    postal_code: str | None = None
    country_code: str | None = None
    formatted: str | None = None
    original: dict[str, Any] = Field(default_factory=dict, exclude=True)


def _coerce_country_code(value: str | None) -> str | None:
    if not value:
        return None
    trimmed = value.strip()
    if len(trimmed) == 2 and trimmed.isalpha():
        return trimmed.upper()
    return trimmed  # longer values pass through (e.g. "United States")


_ADDRESS_FIELD_CANDIDATES: dict[str, tuple[str, ...]] = {
    "line1": ("line1", "address1", "address_line_1", "street", "street_address"),
    "line2": ("line2", "address2", "address_line_2", "street2"),
    "city": ("city", "admin_area_2", "locality"),
    "region": ("state", "province", "region", "admin_area_1", "state_province_region"),
    "postal_code": ("postal_code", "zip", "zipcode", "postcode"),
    "country_code": ("country_code", "country"),
    "formatted": ("formatted", "formatted_address"),
}


def normalize_postal_address(data: dict[str, Any] | None) -> PostalAddress | None:
    """Build a :class:`PostalAddress` from a dict. Returns ``None`` if no
    recognisable fields are present."""
    if not data or not isinstance(data, dict):
        return None

    values: dict[str, Any] = {}
    for canonical, candidates in _ADDRESS_FIELD_CANDIDATES.items():
        for key in candidates:
            if data.get(key):
                values[canonical] = data[key]
                break

    if "country_code" in values:
        values["country_code"] = _coerce_country_code(str(values["country_code"]))

    if not any(values.values()):
        return None
    return PostalAddress(**values, original=data)


# ─────────────────────────── Phone ───────────────────────────


class Phone(BaseModel):
    """E.164 canonical phone number.

    When the full `phonenumbers` library isn't available (it's not a
    Liquid dep), we use a lightweight E.164 heuristic: ``+`` followed by
    7-15 digits. ``raw`` always preserves what the provider returned.
    """

    e164: str | None = None
    country_code: str | None = None
    national_number: str | None = None
    extension: str | None = None
    raw: str
    original: dict[str, Any] = Field(default_factory=dict, exclude=True)


_E164_RE = re.compile(r"^\+([1-9]\d{6,14})$")
_DIGITS_RE = re.compile(r"\D+")


def _parse_e164(raw: str) -> tuple[str | None, str | None, str | None]:
    """Return ``(e164, country_code, national_number)``.

    Recognises ``+15551234567`` and ``15551234567`` (defaults to ``+``
    prefix if length looks right). Returns ``(None, None, None)`` when
    the number can't be confidently parsed.
    """
    stripped = raw.strip()
    if "x" in stripped.lower():
        stripped = stripped.lower().split("x", 1)[0].strip()
    candidate = stripped
    if not candidate.startswith("+"):
        digits_only = _DIGITS_RE.sub("", candidate)
        if 7 <= len(digits_only) <= 15:
            candidate = "+" + digits_only
        else:
            return None, None, None
    else:
        candidate = "+" + _DIGITS_RE.sub("", candidate[1:])

    match = _E164_RE.match(candidate)
    if match is None:
        return None, None, None
    digits = match.group(1)
    # Naive country-code split — 1 char for NANP (+1), else 1-3 chars.
    # Full libphonenumber beats this; callers who care can override.
    if digits.startswith("1") and len(digits) == 11:
        return candidate, "1", digits[1:]
    if digits.startswith(("7",)) and len(digits) == 11:
        return candidate, "7", digits[1:]
    # Conservative: 2-char for most of Europe/Asia.
    return candidate, digits[:2], digits[2:]


def _extract_extension(raw: str) -> str | None:
    lowered = raw.lower()
    for sep in ("ext.", "ext", "x"):
        if sep in lowered:
            tail = raw[lowered.rindex(sep) + len(sep) :].strip(" .")
            ext_digits = _DIGITS_RE.sub("", tail)
            if ext_digits:
                return ext_digits
    return None


def normalize_phone(value: str | dict[str, Any] | None) -> Phone | None:
    """Build a :class:`Phone` from a string or dict. Returns ``None`` for
    unrecognisable input."""
    if value is None:
        return None
    if isinstance(value, dict):
        raw = value.get("e164") or value.get("number") or value.get("phone") or value.get("value")
        if not raw:
            return None
        raw = str(raw)
        original = dict(value)
    else:
        raw = str(value)
        original = {"raw": raw}

    if not raw:
        return None

    # If the input has no digits at all, it isn't a phone number.
    if not any(ch.isdigit() for ch in raw):
        return None

    e164, cc, national = _parse_e164(raw)
    ext = _extract_extension(raw)
    return Phone(
        e164=e164,
        country_code=cc,
        national_number=national,
        extension=ext,
        raw=raw,
        original=original,
    )


# ─────────────────────────── Email ───────────────────────────


class Email(BaseModel):
    """Canonical email. ``address`` is always lowercased for comparability."""

    address: str
    domain: str
    verified: bool | None = None
    primary: bool | None = None
    label: str | None = None
    original: dict[str, Any] = Field(default_factory=dict, exclude=True)


_EMAIL_BASIC_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def normalize_email(value: str | dict[str, Any] | None) -> Email | None:
    """Build an :class:`Email` from a string or dict. Invalid emails → ``None``."""
    if value is None:
        return None
    if isinstance(value, dict):
        address_raw = value.get("address") or value.get("email") or value.get("value")
        if not address_raw:
            return None
        address = str(address_raw).strip().lower()
        verified = value.get("verified")
        primary = value.get("primary")
        label = value.get("label") or value.get("type")
        original = dict(value)
    else:
        address = str(value).strip().lower()
        verified = None
        primary = None
        label = None
        original = {"raw": value}

    if not _EMAIL_BASIC_RE.match(address):
        return None
    domain = address.split("@", 1)[1]
    return Email(
        address=address,
        domain=domain,
        verified=verified,
        primary=primary,
        label=label,
        original=original,
    )


# ─────────────────────────── PersonName ───────────────────────────


class PersonName(BaseModel):
    """Canonical person / organisation name.

    Keeps only ``full``/``given``/``family`` + ``is_organization`` +
    ``display``. Middle names, prefixes, suffixes are too inconsistent
    across providers to round-trip — they live on ``original``.
    """

    full: str
    given: str | None = None
    family: str | None = None
    display: str | None = None
    is_organization: bool = False
    original: dict[str, Any] = Field(default_factory=dict, exclude=True)


_NAME_GIVEN_KEYS = ("first_name", "given_name", "firstname", "given", "first")
_NAME_FAMILY_KEYS = ("last_name", "family_name", "surname", "lastname", "family", "last")
_NAME_FULL_KEYS = ("name", "full_name", "fullname")
_NAME_DISPLAY_KEYS = ("display_name", "displayname", "display")
_NAME_ORG_KEYS = ("business_name", "organization", "company", "company_name")


def _first_hit(data: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        if data.get(key):
            return str(data[key])
    return None


def normalize_person_name(value: str | dict[str, Any] | None) -> PersonName | None:
    """Build a :class:`PersonName` from a dict or plain string."""
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        return PersonName(full=stripped, original={"raw": value})

    if not isinstance(value, dict):
        return None

    given = _first_hit(value, _NAME_GIVEN_KEYS)
    family = _first_hit(value, _NAME_FAMILY_KEYS)
    full = _first_hit(value, _NAME_FULL_KEYS)
    display = _first_hit(value, _NAME_DISPLAY_KEYS)
    org_name = _first_hit(value, _NAME_ORG_KEYS)

    is_org = bool(org_name) and not (given or family)
    if is_org and not full:
        full = org_name

    if not full:
        parts = [p for p in (given, family) if p]
        if parts:
            full = " ".join(parts)
        elif display:
            full = display
    if not full:
        return None
    return PersonName(
        full=full,
        given=given,
        family=family,
        display=display,
        is_organization=is_org,
        original=value,
    )


# ─────────────────────────── FileAttachment ───────────────────────────


class FileAttachment(BaseModel):
    """Canonical file reference."""

    url: str | None = None
    filename: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    original: dict[str, Any] = Field(default_factory=dict, exclude=True)


_FILE_URL_KEYS = ("url", "download_url", "web_url", "webViewLink", "src", "url_private", "file_url")
_FILE_NAME_KEYS = ("name", "filename", "file_name", "title")
_FILE_MIME_KEYS = ("mime_type", "mimeType", "content_type", "contentType")
_FILE_SIZE_KEYS = ("size", "size_bytes", "sizeBytes", "bytes")
_FILE_HASH_KEYS = ("sha256", "content_hash", "hash", "sha")


def normalize_file_attachment(data: dict[str, Any] | None) -> FileAttachment | None:
    """Build a :class:`FileAttachment` from a dict."""
    if not data or not isinstance(data, dict):
        return None
    url = _first_hit(data, _FILE_URL_KEYS)
    filename = _first_hit(data, _FILE_NAME_KEYS)
    mime_type = _first_hit(data, _FILE_MIME_KEYS)
    size_raw = None
    for key in _FILE_SIZE_KEYS:
        if data.get(key) is not None:
            size_raw = data[key]
            break
    size_bytes: int | None
    try:
        size_bytes = int(size_raw) if size_raw is not None else None
    except (TypeError, ValueError):
        size_bytes = None
    sha256 = _first_hit(data, _FILE_HASH_KEYS)

    if not any([url, filename, mime_type, size_bytes, sha256]):
        return None
    return FileAttachment(
        url=url,
        filename=filename,
        mime_type=mime_type,
        size_bytes=size_bytes,
        sha256=sha256,
        original=data,
    )


# ─────────────────────────── UserRef ───────────────────────────


class UserRef(BaseModel):
    """Canonical "who did this" attribution across APIs."""

    id: str
    display_name: str | None = None
    email: str | None = None
    avatar_url: str | None = None
    original: dict[str, Any] = Field(default_factory=dict, exclude=True)


_USER_ID_KEYS = ("id", "user_id", "userId", "login", "username")
_USER_NAME_KEYS = ("display_name", "displayName", "name", "real_name", "full_name", "login")
_USER_EMAIL_KEYS = ("email", "email_address", "primary_email")
_USER_AVATAR_KEYS = ("avatar_url", "avatarUrl", "avatar", "image_url", "picture")


def normalize_user_ref(value: str | dict[str, Any] | None) -> UserRef | None:
    """Build a :class:`UserRef` from an id string or dict."""
    if value is None:
        return None
    if isinstance(value, str):
        if not value.strip():
            return None
        return UserRef(id=value, original={"raw": value})
    if not isinstance(value, dict):
        return None

    user_id = _first_hit(value, _USER_ID_KEYS)
    if not user_id:
        return None
    return UserRef(
        id=user_id,
        display_name=_first_hit(value, _USER_NAME_KEYS),
        email=_first_hit(value, _USER_EMAIL_KEYS),
        avatar_url=_first_hit(value, _USER_AVATAR_KEYS),
        original=value,
    )


# ─────────────────────────── Tag ───────────────────────────


class Tag(BaseModel):
    """Canonical label / tag."""

    name: str
    id: str | None = None
    color: str | None = None
    original: dict[str, Any] = Field(default_factory=dict, exclude=True)


def normalize_tags(value: str | list[Any] | None) -> list[Tag]:
    """Build a list of :class:`Tag` from:

    - a comma-separated string (``"a, b, c"`` — Shopify products);
    - a list of strings (``["a", "b"]``);
    - a list of dicts with ``name``/``id``/``color`` (GitHub labels).
    """
    if value is None:
        return []
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",") if p.strip()]
        return [Tag(name=p, original={"raw": p}) for p in parts]
    if not isinstance(value, list):
        return []

    out: list[Tag] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(Tag(name=item.strip(), original={"raw": item}))
        elif isinstance(item, dict):
            name = item.get("name") or item.get("label") or item.get("title")
            if not name:
                continue
            out.append(
                Tag(
                    name=str(name),
                    id=str(item.get("id")) if item.get("id") is not None else None,
                    color=item.get("color"),
                    original=item,
                )
            )
    return out


# ─────────────────────────── GeoPoint ───────────────────────────


class GeoPoint(BaseModel):
    """Canonical lat/lng point.

    Detects GeoJSON order (``[lng, lat]``) by validating ``lat ∈ [-90, 90]``
    after the first interpretation; if the initial guess would put latitude
    out of range we swap. Ambiguous inputs fall back to ``raw``.
    """

    lat: float
    lng: float
    original: dict[str, Any] = Field(default_factory=dict, exclude=True)


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_geo_point(value: Any) -> GeoPoint | None:
    """Build a :class:`GeoPoint` from a dict (``{lat, lng}`` / ``{lat, lon}``),
    a two-element list (GeoJSON ``[lng, lat]``) / tuple, or a
    ``"lat,lng"`` string."""
    if value is None:
        return None
    original: dict[str, Any]
    if isinstance(value, dict):
        original = dict(value)
        lat = _coerce_float(value.get("lat") or value.get("latitude"))
        lng = _coerce_float(value.get("lng") or value.get("lon") or value.get("long") or value.get("longitude"))
    elif isinstance(value, list | tuple) and len(value) == 2:
        a = _coerce_float(value[0])
        b = _coerce_float(value[1])
        if a is None or b is None:
            return None
        # Two interpretations: [lat,lng] or [lng,lat]. If a is out of lat
        # range but b is in range, swap. If both plausibly in lat range,
        # prefer the GeoJSON convention [lng,lat] as the canonical list
        # interpretation (matches Mapbox/Turf/Shapely).
        if -90 <= a <= 90 and -90 <= b <= 90:
            lng, lat = a, b  # GeoJSON default
        elif -90 <= a <= 90:
            lat, lng = a, b
        else:
            lng, lat = a, b
        original = {"raw": list(value)}
    elif isinstance(value, str):
        parts = [p.strip() for p in value.split(",")]
        if len(parts) != 2:
            return None
        lat_raw, lng_raw = parts
        lat = _coerce_float(lat_raw)
        lng = _coerce_float(lng_raw)
        original = {"raw": value}
    else:
        return None

    if lat is None or lng is None:
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return GeoPoint(lat=lat, lng=lng, original=original)
