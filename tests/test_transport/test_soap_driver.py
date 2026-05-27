"""SOAP driver: builds an envelope, posts to the WSDL service endpoint, parses
the XML response into records, and turns SOAP Faults into fetch failures."""

import httpx
import pytest

from liquid.exceptions import SyncRuntimeError, VaultError
from liquid.models.schema import Endpoint
from liquid.sync.fetcher import Fetcher
from liquid.transport.soap import _build_envelope


class FakeVault:
    async def store(self, key, value):
        pass

    async def get(self, key):
        raise VaultError(key)

    async def delete(self, key):
        pass


META = {
    "soap_version": "1.1",
    "endpoint": "http://example.com/weather.asmx",
    "soap_action": "http://example.com/weather/GetForecast",
    "request_element": "GetForecast",
    "request_namespace": "http://example.com/weather",
    "param_names": ["City"],
}


def _endpoint() -> Endpoint:
    return Endpoint(path="/soap#GetForecast", protocol="soap", method="POST", transport_meta=META)


def test_build_envelope_1_1():
    xml = _build_envelope(META, {"City": "NYC"}, "1.1")
    assert "<soapenv:Envelope" in xml
    assert '<tns:GetForecast xmlns:tns="http://example.com/weather">' in xml
    assert "<tns:City>NYC</tns:City>" in xml


def test_build_envelope_escapes_values():
    xml = _build_envelope(META, {"City": "A & B <x>"}, "1.1")
    assert "A &amp; B &lt;x&gt;" in xml


async def _run(handler, *, extra_params=None):
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        fetcher = Fetcher(http_client=client, vault=FakeVault())
        return await fetcher.fetch(
            endpoint=_endpoint(),
            base_url="http://example.com/weather.asmx?wsdl",
            auth_ref="none",
            extra_params=extra_params,
        )


async def test_soap_fetch_extracts_record_list():
    def handler(req: httpx.Request) -> httpx.Response:
        assert str(req.url) == "http://example.com/weather.asmx"  # service location, not base_url
        assert req.headers["soapaction"] == '"http://example.com/weather/GetForecast"'
        assert b"<tns:City>NYC</tns:City>" in req.content
        body = """<?xml version="1.0"?>
        <soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
          <soap:Body>
            <GetForecastResponse xmlns="http://example.com/weather">
              <Forecast><Day>Mon</Day><High>20</High></Forecast>
              <Forecast><Day>Tue</Day><High>22</High></Forecast>
            </GetForecastResponse>
          </soap:Body>
        </soap:Envelope>"""
        return httpx.Response(200, text=body, headers={"content-type": "text/xml"})

    result = await _run(handler, extra_params={"City": "NYC"})
    assert result.records == [{"Day": "Mon", "High": "20"}, {"Day": "Tue", "High": "22"}]


async def test_soap_fault_raises():
    def handler(req: httpx.Request) -> httpx.Response:
        body = """<?xml version="1.0"?>
        <soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
          <soap:Body>
            <soap:Fault><faultcode>Server</faultcode><faultstring>boom</faultstring></soap:Fault>
          </soap:Body>
        </soap:Envelope>"""
        return httpx.Response(200, text=body, headers={"content-type": "text/xml"})

    with pytest.raises(SyncRuntimeError):
        await _run(handler)
