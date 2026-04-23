"""0.25 Intent + normalizer breadth — 71 intents / 12 normalizers.

Intents are research-backed across the top 3-5 APIs in each domain
(Stripe/Square/PayPal for payments, HubSpot/Salesforce/Pipedrive for
CRM, Shopify/WooCommerce/BigCommerce for commerce, etc.). Each carries
a canonical JSON schema; adapters bind the canonical fields to
provider-specific action/endpoint fields.

Normalizers cover the shapes that agents waste tokens reconciling:
addresses, phones, emails, person names, file attachments, user
references, tags, and geo coordinates. Each preserves ``original``
verbatim, exactly like the 0.14 ``Money`` shape.
"""

from __future__ import annotations

from liquid.intent import list_intents
from liquid.normalize import (
    normalize_email,
    normalize_file_attachment,
    normalize_geo_point,
    normalize_person_name,
    normalize_phone,
    normalize_postal_address,
    normalize_tags,
    normalize_user_ref,
)


def main() -> None:
    print("=== Intent registry (by namespace) ===")
    namespaces = [
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
    ]
    for ns in namespaces:
        names = [i.name for i in list_intents(namespace=ns)]
        print(f"  {ns:>10} ({len(names):>2}): {', '.join(names)}")

    print("\n=== Normalizer round-trips ===")

    addr = normalize_postal_address(
        {
            "address_line_1": "1 Main St",
            "admin_area_2": "Austin",
            "admin_area_1": "TX",
            "postal_code": "78701",
            "country_code": "us",
        }
    )
    print("  PostalAddress (PayPal shape → canonical):")
    print(f"    {addr.model_dump()}" if addr else "    None")

    phone = normalize_phone("+1 (415) 555-0100 ext. 42")
    print("  Phone (formatted → E.164 + extension):")
    print(f"    {phone.model_dump()}" if phone else "    None")

    email = normalize_email({"email": "Alice@Example.com", "verified": True, "primary": True})
    print("  Email (GitHub shape → canonical):")
    print(f"    {email.model_dump()}" if email else "    None")

    name = normalize_person_name({"given_name": "Grace", "surname": "Hopper"})
    print("  PersonName (PayPal shape → canonical):")
    print(f"    {name.model_dump()}" if name else "    None")

    f = normalize_file_attachment(
        {"name": "spec.pdf", "mimeType": "application/pdf", "size": "4096", "webViewLink": "https://..."}
    )
    print("  FileAttachment (Drive shape → canonical):")
    print(f"    {f.model_dump()}" if f else "    None")

    user = normalize_user_ref({"id": 42, "login": "ada", "avatar_url": "https://...", "email": "ada@example.com"})
    print("  UserRef (GitHub shape → canonical):")
    print(f"    {user.model_dump()}" if user else "    None")

    tags = normalize_tags("vip, new, subscribed")
    print("  Tag (Shopify comma-string → list):")
    print(f"    {[t.model_dump() for t in tags]}")

    point = normalize_geo_point([-122.4194, 37.7749])
    print("  GeoPoint (GeoJSON [lng,lat] → canonical):")
    print(f"    {point.model_dump()}" if point else "    None")


if __name__ == "__main__":
    main()
