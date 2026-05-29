"""Connectors — the agent's senses and hands pointed at *people*.

Most of Liquid connects an agent to machines (APIs, databases, other agents). A
connector closes the last gap: a human as a node the agent can perceive and act
on. Each connector exposes the same shape — ``sense()`` yields incoming messages
as :class:`~liquid.transport.SenseEvent`s (compose with ``react`` /
``merge_senses``), and an action method (``send``) is the hands.
"""

from liquid.connectors.home_assistant import HomeAssistantConnector
from liquid.connectors.smartcar import SmartcarConnector
from liquid.connectors.telegram import TelegramConnector

__all__ = ["HomeAssistantConnector", "SmartcarConnector", "TelegramConnector"]
