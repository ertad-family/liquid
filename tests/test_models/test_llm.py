from liquid.models import DeliveryResult, LLMResponse, MappedRecord, Message, Tool, ToolCall


class TestMessage:
    def test_user_message(self):
        msg = Message(role="user", content="hello")
        assert msg.tool_call_id is None
        assert msg.tool_calls is None

    def test_assistant_with_tool_calls(self):
        tc = ToolCall(id="call_1", name="search", arguments={"q": "test"})
        msg = Message(role="assistant", content="", tool_calls=[tc])
        assert len(msg.tool_calls) == 1
        assert msg.tool_calls[0].name == "search"


class TestTool:
    def test_basic(self):
        tool = Tool(name="fetch", description="Fetch data", parameters={"type": "object"})
        assert tool.name == "fetch"


class TestLLMResponse:
    def test_content_only(self):
        resp = LLMResponse(content="The API has 3 endpoints")
        assert resp.tool_calls is None

    def test_tool_calls_only(self):
        resp = LLMResponse(tool_calls=[ToolCall(id="1", name="map_field")])
        assert resp.content is None
        assert len(resp.tool_calls) == 1


class TestMappedRecord:
    def test_basic(self):
        rec = MappedRecord(
            source_endpoint="/orders",
            source_data={"total_price": "100.00"},
            mapped_data={"amount": 100.0},
        )
        assert rec.mapping_errors is None


class TestDeliveryResult:
    def test_defaults(self):
        dr = DeliveryResult()
        assert dr.delivered == 0
        assert dr.failed == 0

    def test_with_errors(self):
        dr = DeliveryResult(delivered=5, failed=2, errors=["timeout", "bad record"])
        assert len(dr.errors) == 2
