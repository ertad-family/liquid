# Containerized open-source Liquid MCP server (stdio transport).
#
#   docker build -t liquid-mcp .
#   docker run --rm -i -e OPENAI_API_KEY=sk-... liquid-mcp
#
# Speaks MCP over stdio. Introspection (initialize / list_tools) works with no
# config; discovery (liquid_connect / liquid_discover) needs an LLM key —
# OPENAI_API_KEY (or GEMINI_API_KEY / ANTHROPIC_API_KEY, or a local OPENAI_BASE_URL).
FROM python:3.12-slim

RUN pip install --no-cache-dir "liquid-api[mcp]"

# Persist adapters/credentials across runs by mounting a volume at /root/.liquid.
ENTRYPOINT ["liquid-mcp"]
