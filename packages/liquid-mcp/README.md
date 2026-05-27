# liquid-mcp

Self-hosted [**Liquid**](https://github.com/ertad-family/liquid) MCP server —
**connect your agent to any HTTP API on the fly.** Liquid discovers and maps any
REST API once, then fetches typed data deterministically (no per-call LLM).

```bash
uvx liquid-mcp                       # zero-install run
# or:
pip install liquid-mcp && liquid-mcp

export OPENAI_API_KEY=sk-...         # or GEMINI_API_KEY / ANTHROPIC_API_KEY,
                                     # or OPENAI_BASE_URL=http://localhost:11434/v1 (local)
```

Tools: `liquid_connect`, `liquid_fetch`, `liquid_query`, `liquid_discover`,
`liquid_list_adapters`. Adapters and credentials persist under `~/.liquid`.

This is a thin launcher over [`liquid-api[mcp]`](https://pypi.org/project/liquid-api/);
all engine code lives there. Full docs: https://github.com/ertad-family/liquid

<!-- mcp-name: io.github.ertad-family/liquid -->
