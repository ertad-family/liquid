# MCP Servers Registry Entry

For submission to https://github.com/anthropics/mcp-servers

## Entry format

When anthropics/mcp-servers accepts a PR, add this entry to the community servers list:

### Liquid

**Description:** Connect AI agents to anything — web APIs, other agents (MCP/A2A), and databases (SQL, graph, document, key-value) — through one read/write interface. AI-powered discovery, self-healing integrations, 2,500+ pre-discovered APIs. Runs self-hosted in your own process (no account, no key to a Liquid service); bring your own LLM key.

**Install (zero-install with uvx):**
```bash
export OPENAI_API_KEY=sk-...        # or GEMINI_API_KEY / ANTHROPIC_API_KEY / local OPENAI_BASE_URL
uvx liquid-mcp                      # or: pip install liquid-api && liquid-mcp
```

**Tools exposed:**
- `liquid_connect` — discover + map any interface into a reusable adapter
- `liquid_fetch` — read records from a connected adapter
- `liquid_query` — server-side search / aggregate (token-efficient)
- `liquid_estimate` — pre-flight cost/size, no call made
- `liquid_discover` — inspect an interface without saving an adapter
- `liquid_list_adapters` — list connected adapters
- `liquid_execute` — write (database insert/update/delete) — **only when started with `LIQUID_ALLOW_WRITES=1`**; the default surface is read-only

**Repository:** https://github.com/ertad-family/liquid
**License:** AGPL-3.0

**Example Claude Desktop config:**
```json
{
  "mcpServers": {
    "liquid": {
      "command": "uvx",
      "args": ["liquid-mcp"],
      "env": { "OPENAI_API_KEY": "sk-..." }
    }
  }
}
```

## Submission checklist

- [ ] Fork https://github.com/anthropics/mcp-servers
- [ ] Add entry to appropriate community list file
- [ ] Include screenshot of Claude Desktop using Liquid tools
- [ ] Open PR with title "Add Liquid — connect agents to any interface (APIs, databases, agents)"
