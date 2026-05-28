# Liquid — Claude Desktop bundle (`.mcpb`)

One-click install of the open-source Liquid MCP server in Claude Desktop. The
bundle (an `.mcpb` zip with `manifest.json`) prompts the user for a model key on
install and runs the server locally via `uvx liquid-mcp` — no JSON to hand-edit.

## How it runs

The manifest launches `uvx liquid-mcp` (the [`liquid-mcp`](https://pypi.org/project/liquid-mcp/)
package → `liquid.mcp_server:main`). It does **not** bundle Python dependencies —
Liquid's deps include compiled wheels (e.g. `pydantic-core`) that can't be vendored
cross-platform — so `uv` resolves them per machine at first run.

**Prerequisite:** `uv` must be installed (provides `uvx`). Install once:
`curl -LsSf https://astral.sh/uv/install.sh | sh` (macOS/Linux) or
`winget install astral-sh.uv` (Windows). Keys collected at install time are stored
in the OS keychain (`sensitive: true`) and injected as env vars.

## Build the `.mcpb`

```bash
# from this directory (packages/liquid-mcp/mcpb)
npx @anthropic-ai/mcpb validate manifest.json   # schema check
npx @anthropic-ai/mcpb pack .                    # validates + zips → an .mcpb file
```

`mcpb pack` validates the manifest and produces an `.mcpb` archive (the built
artifact is git-ignored — build it at release time). Equivalent without the CLI:
zip this directory with `manifest.json` at the archive root.

## Install

Double-click `liquid.mcpb`, or in Claude Desktop → **Settings → Extensions →
Advanced → Install Extension…** → pick the file. Fill in a model key
(OpenAI / Gemini / Anthropic, or a local OpenAI-compatible Base URL) and,
optionally, toggle **Allow database writes**.

## Tools exposed

`liquid_connect`, `liquid_fetch`, `liquid_query`, `liquid_estimate`,
`liquid_discover`, `liquid_list_adapters` — and `liquid_execute` (DB writes) only
when **Allow database writes** is on (`LIQUID_ALLOW_WRITES=1`).

> ⚠️ **Test before publishing.** Verify a real install in Claude Desktop on each
> target OS (the desktop runtime's handling of `uvx`/PATH can't be checked in CI).
> The manifest is validated by `mcpb pack`, but the end-to-end install/launch
> should be confirmed manually.
