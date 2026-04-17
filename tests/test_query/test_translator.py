from liquid.models.schema import Endpoint, EndpointKind, Parameter, ParameterLocation
from liquid.query.translator import translate_to_params


def _make_endpoint(param_names: list[str]) -> Endpoint:
    return Endpoint(
        path="/orders",
        method="GET",
        kind=EndpointKind.READ,
        parameters=[Parameter(name=name, location=ParameterLocation.QUERY, required=False) for name in param_names],
    )


class TestTranslator:
    def test_implicit_eq_becomes_param(self):
        ep = _make_endpoint(["status"])
        native, remain = translate_to_params({"status": "paid"}, ep)
        assert native == {"status": "paid"}
        assert remain == {}

    def test_explicit_eq_becomes_param(self):
        ep = _make_endpoint(["status"])
        native, _remain = translate_to_params({"status": {"$eq": "paid"}}, ep)
        assert native == {"status": "paid"}

    def test_unsupported_op_stays_local(self):
        ep = _make_endpoint(["status"])
        native, remain = translate_to_params({"status": {"$ne": "paid"}}, ep)
        assert native == {}
        assert remain == {"status": {"$ne": "paid"}}

    def test_unknown_field_stays_local(self):
        ep = _make_endpoint(["status"])
        native, remain = translate_to_params({"total": {"$gt": 100}}, ep)
        assert native == {}
        assert remain == {"total": {"$gt": 100}}

    def test_in_becomes_comma_param(self):
        ep = _make_endpoint(["status"])
        native, _remain = translate_to_params({"status": {"$in": ["paid", "pending"]}}, ep)
        assert native == {"status": "paid,pending"}

    def test_logical_stays_local(self):
        ep = _make_endpoint(["status"])
        _native, remain = translate_to_params({"$or": [{"status": "paid"}]}, ep)
        assert "$or" in remain
