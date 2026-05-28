"""Entry point declared by the Liquid MCP bundle (manifest `server.entry_point`).

The bundle actually launches the server via ``uvx liquid-mcp`` (see the manifest
``mcp_config``), which installs ``liquid-api`` and runs ``liquid.mcp_server:main``
in an isolated environment — so this file is normally not executed. It exists so
the bundle is self-describing and still works if a host runs the entry point with
a Python that already has ``liquid-api`` importable.
"""

from liquid.mcp_server import main

if __name__ == "__main__":
    main()
