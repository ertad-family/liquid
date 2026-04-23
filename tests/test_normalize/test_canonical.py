"""Unit tests for the eight 0.25.0 canonical normalizers."""

from __future__ import annotations

from liquid.normalize.canonical import (
    normalize_email,
    normalize_file_attachment,
    normalize_geo_point,
    normalize_person_name,
    normalize_phone,
    normalize_postal_address,
    normalize_tags,
    normalize_user_ref,
)


class TestPostalAddress:
    def test_stripe_shape(self) -> None:
        addr = normalize_postal_address(
            {
                "line1": "510 Townsend St",
                "city": "San Francisco",
                "state": "CA",
                "postal_code": "94103",
                "country": "US",
            }
        )
        assert addr is not None
        assert addr.line1 == "510 Townsend St"
        assert addr.region == "CA"
        assert addr.postal_code == "94103"
        assert addr.country_code == "US"

    def test_paypal_shape(self) -> None:
        addr = normalize_postal_address(
            {
                "address_line_1": "1 Main St",
                "admin_area_2": "Austin",
                "admin_area_1": "TX",
                "postal_code": "78701",
                "country_code": "us",
            }
        )
        assert addr is not None
        assert addr.line1 == "1 Main St"
        assert addr.city == "Austin"
        assert addr.region == "TX"
        assert addr.country_code == "US"  # uppercased

    def test_shopify_shape(self) -> None:
        addr = normalize_postal_address(
            {"address1": "150 Elgin", "province": "ON", "zip": "K2P 1L4", "country_code": "CA"}
        )
        assert addr is not None
        assert addr.line1 == "150 Elgin"
        assert addr.region == "ON"
        assert addr.postal_code == "K2P 1L4"
        assert addr.country_code == "CA"

    def test_empty_returns_none(self) -> None:
        assert normalize_postal_address(None) is None
        assert normalize_postal_address({}) is None
        assert normalize_postal_address({"unrelated": 1}) is None

    def test_original_preserved_hidden_from_dump(self) -> None:
        addr = normalize_postal_address({"line1": "x", "country_code": "US"})
        assert addr is not None
        assert addr.original["line1"] == "x"
        # ``exclude=True`` means ``original`` is not in ``model_dump``.
        assert "original" not in addr.model_dump()


class TestPhone:
    def test_e164_string(self) -> None:
        phone = normalize_phone("+15551234567")
        assert phone is not None
        assert phone.e164 == "+15551234567"
        assert phone.country_code == "1"
        assert phone.national_number == "5551234567"

    def test_formatted_us_number(self) -> None:
        phone = normalize_phone("(555) 123-4567")
        assert phone is not None
        assert phone.e164 == "+5551234567"  # no country code supplied → best-effort
        assert phone.raw == "(555) 123-4567"

    def test_extension_extracted(self) -> None:
        phone = normalize_phone("+15551234567 x1234")
        assert phone is not None
        assert phone.extension == "1234"

    def test_dict_input(self) -> None:
        phone = normalize_phone({"value": "+442071838750", "type": "mobile"})
        assert phone is not None
        assert phone.e164 == "+442071838750"
        assert phone.original["type"] == "mobile"

    def test_garbage_returns_none(self) -> None:
        assert normalize_phone(None) is None
        assert normalize_phone("") is None
        # "abc" has 0 digits; heuristic bails.
        assert normalize_phone("abc") is None


class TestEmail:
    def test_bare_string(self) -> None:
        email = normalize_email("Alice@Example.com")
        assert email is not None
        assert email.address == "alice@example.com"
        assert email.domain == "example.com"

    def test_github_shape(self) -> None:
        email = normalize_email({"email": "bob@example.com", "verified": True, "primary": True, "visibility": "public"})
        assert email is not None
        assert email.verified is True
        assert email.primary is True
        assert email.address == "bob@example.com"

    def test_invalid_returns_none(self) -> None:
        assert normalize_email(None) is None
        assert normalize_email("not-an-email") is None
        assert normalize_email({}) is None


class TestPersonName:
    def test_first_last_split(self) -> None:
        name = normalize_person_name({"first_name": "Jane", "last_name": "Doe"})
        assert name is not None
        assert name.given == "Jane"
        assert name.family == "Doe"
        assert name.full == "Jane Doe"
        assert name.is_organization is False

    def test_single_string(self) -> None:
        name = normalize_person_name("Grace Hopper")
        assert name is not None
        assert name.full == "Grace Hopper"

    def test_paypal_given_surname(self) -> None:
        name = normalize_person_name({"given_name": "John", "surname": "Smith"})
        assert name is not None
        assert name.given == "John"
        assert name.family == "Smith"
        assert name.full == "John Smith"

    def test_organization_fallback(self) -> None:
        name = normalize_person_name({"business_name": "Acme Inc."})
        assert name is not None
        assert name.is_organization is True
        assert name.full == "Acme Inc."

    def test_display_fallback(self) -> None:
        name = normalize_person_name({"display_name": "alice_42"})
        assert name is not None
        assert name.full == "alice_42"
        assert name.display == "alice_42"

    def test_empty_returns_none(self) -> None:
        assert normalize_person_name(None) is None
        assert normalize_person_name("") is None
        assert normalize_person_name({}) is None


class TestFileAttachment:
    def test_github_contents(self) -> None:
        f = normalize_file_attachment(
            {"name": "README.md", "path": "README.md", "size": 1024, "download_url": "https://.../README.md"}
        )
        assert f is not None
        assert f.filename == "README.md"
        assert f.size_bytes == 1024
        assert f.url == "https://.../README.md"

    def test_slack_file(self) -> None:
        f = normalize_file_attachment(
            {"name": "screenshot.png", "mimetype": "image/png", "size": 512, "url_private": "https://..."}
        )
        assert f is not None
        assert f.mime_type is None  # slack uses ``mimetype`` (no underscore) — not in our key list
        assert f.filename == "screenshot.png"
        assert f.url == "https://..."

    def test_google_drive(self) -> None:
        f = normalize_file_attachment(
            {"name": "report.pdf", "mimeType": "application/pdf", "size": "2048", "webViewLink": "https://..."}
        )
        assert f is not None
        assert f.mime_type == "application/pdf"
        assert f.size_bytes == 2048  # string coerced
        assert f.url == "https://..."

    def test_empty_returns_none(self) -> None:
        assert normalize_file_attachment({}) is None
        assert normalize_file_attachment(None) is None


class TestUserRef:
    def test_github_author(self) -> None:
        u = normalize_user_ref({"id": 42, "login": "ada", "avatar_url": "https://...", "email": "ada@example.com"})
        assert u is not None
        assert u.id == "42"
        assert u.display_name == "ada"
        assert u.email == "ada@example.com"
        assert u.avatar_url == "https://..."

    def test_slack_id_only_string(self) -> None:
        u = normalize_user_ref("U123ABC")
        assert u is not None
        assert u.id == "U123ABC"
        assert u.display_name is None

    def test_stripe_customer_id(self) -> None:
        u = normalize_user_ref("cus_abc123")
        assert u is not None
        assert u.id == "cus_abc123"

    def test_empty_returns_none(self) -> None:
        assert normalize_user_ref(None) is None
        assert normalize_user_ref("") is None
        assert normalize_user_ref({}) is None


class TestTag:
    def test_comma_separated_string(self) -> None:
        """Shopify returns tags as ``'a, b, c'``."""
        tags = normalize_tags("vip, new, subscribed")
        assert [t.name for t in tags] == ["vip", "new", "subscribed"]

    def test_list_of_strings(self) -> None:
        tags = normalize_tags(["a", "b"])
        assert [t.name for t in tags] == ["a", "b"]

    def test_github_labels(self) -> None:
        tags = normalize_tags(
            [
                {"id": 1, "name": "bug", "color": "d73a4a"},
                {"id": 2, "name": "enhancement", "color": "a2eeef"},
            ]
        )
        assert [t.name for t in tags] == ["bug", "enhancement"]
        assert tags[0].color == "d73a4a"
        assert tags[0].id == "1"

    def test_empty_inputs(self) -> None:
        assert normalize_tags(None) == []
        assert normalize_tags("") == []
        assert normalize_tags([]) == []


class TestGeoPoint:
    def test_dict_lat_lng(self) -> None:
        p = normalize_geo_point({"lat": 37.7749, "lng": -122.4194})
        assert p is not None
        assert p.lat == 37.7749
        assert p.lng == -122.4194

    def test_openweather_lat_lon(self) -> None:
        p = normalize_geo_point({"lat": 51.5074, "lon": -0.1278})
        assert p is not None
        assert p.lat == 51.5074
        assert p.lng == -0.1278

    def test_geojson_array(self) -> None:
        """GeoJSON is ``[lng, lat]`` — the trap we're solving."""
        p = normalize_geo_point([-122.4194, 37.7749])
        assert p is not None
        assert p.lat == 37.7749
        assert p.lng == -122.4194

    def test_string_form(self) -> None:
        p = normalize_geo_point("37.7749,-122.4194")
        assert p is not None
        assert p.lat == 37.7749
        assert p.lng == -122.4194

    def test_out_of_range_rejected(self) -> None:
        assert normalize_geo_point({"lat": 200, "lng": 0}) is None
        assert normalize_geo_point({"lat": 0, "lng": 500}) is None

    def test_invalid_returns_none(self) -> None:
        assert normalize_geo_point(None) is None
        assert normalize_geo_point("one,two") is None
        assert normalize_geo_point([1, 2, 3]) is None


class TestRegistryCounts:
    """Smoke test — 0.25.0 ships 71 total canonical intents across 11 namespaces."""

    def test_intent_count(self) -> None:
        from liquid.intent.registry import CANONICAL_INTENTS

        assert len(CANONICAL_INTENTS) == 71

    def test_namespaces_populated(self) -> None:
        from liquid.intent.registry import CANONICAL_INTENTS

        namespaces = {i.namespace for i in CANONICAL_INTENTS.values()}
        expected = {
            "payments",
            "crm",
            "commerce",
            "messaging",
            "ticket",
            "file",
            "calendar",
            "pulls",
            "ci",
            "releases",
            "analytics",
        }
        assert expected.issubset(namespaces)

    def test_post_message_alias_resolves_to_send_message(self) -> None:
        from liquid.intent.registry import get_intent

        intent = get_intent("post_message")
        assert intent is not None
        assert intent.name == "send_message"

    def test_charge_customer_has_new_fields(self) -> None:
        """0.25.0 adds payment_method_id and capture_method."""
        from liquid.intent.registry import get_intent

        intent = get_intent("charge_customer")
        assert intent is not None
        props = intent.canonical_schema["properties"]
        assert "payment_method_id" in props
        assert "capture_method" in props
        assert props["capture_method"]["enum"] == ["automatic", "manual"]
