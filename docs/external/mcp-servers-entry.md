# MCP Servers Registry Entry

For submission to https://github.com/anthropics/mcp-servers

## Entry format

When anthropics/mcp-servers accepts a PR, add this entry to the community servers list:

### Liquid

**Description:** Connect AI agents to any API on the fly. AI-powered discovery, self-healing integrations, 2,500+ pre-discovered APIs. Liquid exposes catalog search, adapter management, fetch, and execute as MCP tools.

**Install:**
```bash
# Get API key at https://liquid.ertad.family/dashboard
pip install liquid-api
python -m mcp_server.server <your-lq-api-key>
```

**Tools exposed:**
- `liquid_connect` — discover + configure an API adapter
- `liquid_fetch` — read data from any configured adapter
- `liquid_execute` — write data (create/update/delete) via verified actions
- `liquid_execute_batch` — batch writes with concurrency
- `liquid_propose_actions` — AI-propose write mappings
- `liquid_configure_action` — verify + save write action
- `liquid_discover` — raw API discovery without saving
- `liquid_list_integrations` — list connected adapters

**Repository:** https://github.com/ertad-family/liquid
**Server URL:** https://liquid.ertad.family/mcp
**License:** AGPL-3.0

**Example Claude Desktop config:**
```json
{
  "mcpServers": {
    "liquid": {
      "command": "python",
      "args": ["-m", "mcp_server.server", "lq-YOUR_API_KEY"]
    }
  }
}
```

## Submission checklist

- [ ] Fork https://github.com/anthropics/mcp-servers
- [ ] Add entry to appropriate community list file
- [ ] Include screenshot of Claude Desktop using Liquid tools
- [ ] Open PR with title "Add Liquid — API integration fabric for agents"
