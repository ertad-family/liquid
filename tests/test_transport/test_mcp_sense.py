"""MCP notifications as sense. The notification→event shaping is verified
deterministically against real ``mcp.types`` notifications; the live push loop
needs a server that emits notifications (no public one), so it's not exercised
end-to-end here — the shaping helper is the part with logic worth testing."""

from __future__ import annotations

from liquid.transport.mcp_driver import _notification_to_event


def test_resource_updated_notification_becomes_event():
    from mcp.types import ResourceUpdatedNotification, ResourceUpdatedNotificationParams, ServerNotification

    notif = ServerNotification(
        ResourceUpdatedNotification(
            method="notifications/resources/updated",
            params=ResourceUpdatedNotificationParams(uri="file:///data.json"),
        )
    )
    event = _notification_to_event(notif, "/mcp")
    assert event is not None
    assert event.modality == "message"
    assert event.source == "/mcp"
    assert event.payload["method"] == "notifications/resources/updated"
    assert event.payload["params"]["uri"] == "file:///data.json"


def test_logging_message_notification_becomes_event():
    from mcp.types import LoggingMessageNotification, LoggingMessageNotificationParams, ServerNotification

    notif = ServerNotification(
        LoggingMessageNotification(
            method="notifications/message",
            params=LoggingMessageNotificationParams(level="info", data="hello"),
        )
    )
    event = _notification_to_event(notif, "/mcp")
    assert event is not None
    assert event.payload["method"] == "notifications/message"
    assert event.payload["params"]["data"] == "hello"
    assert event.payload["params"]["level"] == "info"


def test_tool_list_changed_notification_with_no_params():
    from mcp.types import ServerNotification, ToolListChangedNotification

    notif = ServerNotification(ToolListChangedNotification(method="notifications/tools/list_changed"))
    event = _notification_to_event(notif, "/mcp")
    assert event is not None
    assert event.payload["method"] == "notifications/tools/list_changed"
    assert event.payload["params"] == {}


def test_exception_is_not_an_event():
    assert _notification_to_event(RuntimeError("boom"), "/mcp") is None


def test_non_notification_object_is_ignored():
    # A request-responder or arbitrary object without a notification method → None.
    class FakeResponder:
        request = object()

    assert _notification_to_event(FakeResponder(), "/mcp") is None
