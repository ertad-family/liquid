"""Surface parity: the mail drivers' sense/send capabilities reach to_tools / drivers.

Guards the drift the memory note warns about — a capability that exists on the
driver but never surfaces to agents. An IMAP read endpoint must yield a ``sense_``
tool, and the SMTP driver must advertise write.
"""

from __future__ import annotations

from liquid.models.adapter import AdapterConfig, SyncConfig
from liquid.models.schema import APISchema, AuthRequirement, Endpoint, EndpointKind
from liquid.transport import get_driver, supports_sense, supports_write


def _imap_adapter() -> AdapterConfig:
    schema = APISchema(
        source_url="imap://imap.ex.com",
        service_name="imap-ex",
        discovery_method="email",
        endpoints=[
            Endpoint(
                path="/INBOX",
                method="GET",
                protocol="imap",
                kind=EndpointKind.READ,
                description="IMAP mailbox INBOX",
                transport_meta={"kind": "mailbox", "mailbox": "INBOX"},
            )
        ],
        auth=AuthRequirement(type="basic", tier="C"),
    )
    return AdapterConfig(schema=schema, auth_ref="liquid/imap-ex", mappings=[], sync=SyncConfig(endpoints=["/INBOX"]))


def test_imap_read_endpoint_surfaces_sense_tool():
    tools = _imap_adapter().to_tools(format="anthropic", style="raw")
    names = {t["name"] for t in tools}
    assert "list_INBOX" in names or "list_inbox" in names
    assert any(n.startswith("sense_") for n in names), names


def test_smtp_driver_advertises_write():
    assert supports_write(get_driver("smtp"))
    assert not supports_sense(get_driver("smtp"))


def test_imap_driver_advertises_sense():
    assert supports_sense(get_driver("imap"))
    assert not supports_write(get_driver("imap"))
