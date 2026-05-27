"""WSDL discovery: parse a document/literal WSDL into SOAP endpoints with the
metadata the SOAP driver needs (service location, soapAction, request element)."""

import httpx

from liquid.discovery.wsdl import WSDLDiscovery

WSDL = """<?xml version="1.0" encoding="utf-8"?>
<definitions xmlns="http://schemas.xmlsoap.org/wsdl/"
             xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
             xmlns:xsd="http://www.w3.org/2001/XMLSchema"
             xmlns:tns="http://example.com/weather"
             targetNamespace="http://example.com/weather">
  <types>
    <xsd:schema targetNamespace="http://example.com/weather">
      <xsd:element name="GetForecast">
        <xsd:complexType><xsd:sequence>
          <xsd:element name="City" type="xsd:string"/>
          <xsd:element name="Days" type="xsd:int"/>
        </xsd:sequence></xsd:complexType>
      </xsd:element>
      <xsd:element name="GetForecastResponse">
        <xsd:complexType><xsd:sequence>
          <xsd:element name="Forecasts" type="xsd:string"/>
        </xsd:sequence></xsd:complexType>
      </xsd:element>
    </xsd:schema>
  </types>
  <message name="GetForecastSoapIn"><part name="parameters" element="tns:GetForecast"/></message>
  <message name="GetForecastSoapOut"><part name="parameters" element="tns:GetForecastResponse"/></message>
  <portType name="WeatherSoap">
    <operation name="GetForecast">
      <input message="tns:GetForecastSoapIn"/>
      <output message="tns:GetForecastSoapOut"/>
    </operation>
  </portType>
  <binding name="WeatherSoap" type="tns:WeatherSoap">
    <soap:binding transport="http://schemas.xmlsoap.org/soap/http" style="document"/>
    <operation name="GetForecast">
      <soap:operation soapAction="http://example.com/weather/GetForecast"/>
      <input><soap:body use="literal"/></input>
      <output><soap:body use="literal"/></output>
    </operation>
  </binding>
  <service name="WeatherService">
    <port name="WeatherSoap" binding="tns:WeatherSoap">
      <soap:address location="http://example.com/weather.asmx"/>
    </port>
  </service>
</definitions>
"""


async def test_wsdl_discovery():
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text=WSDL))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await WSDLDiscovery(http_client=client).discover("http://example.com/weather.asmx?wsdl")

    assert result is not None
    assert result.discovery_method == "soap"
    assert result.service_name == "WeatherService"

    ep = next(e for e in result.endpoints if e.path == "/soap#GetForecast")
    assert ep.protocol == "soap"
    assert ep.method == "POST"
    meta = ep.transport_meta
    assert meta["soap_version"] == "1.1"
    assert meta["endpoint"] == "http://example.com/weather.asmx"
    assert meta["soap_action"] == "http://example.com/weather/GetForecast"
    assert meta["request_element"] == "GetForecast"
    assert meta["request_namespace"] == "http://example.com/weather"
    assert meta["param_names"] == ["City", "Days"]


async def test_non_wsdl_returns_none():
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text="<html>not wsdl</html>"))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await WSDLDiscovery(http_client=client).discover("http://example.com/page")
    assert result is None
