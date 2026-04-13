# Launch Posts — Ready to Publish

## 1. Hacker News (Show HN)

**Title:**
```
Show HN: Liquid – Point at a URL, AI discovers the API, code syncs data forever
```

**Link:** `https://github.com/ertad-family/liquid`

**First comment (post immediately after submitting):**

```
Hey HN, I built Liquid because I was tired of writing custom API adapters for every SaaS service.

The core idea: AI runs once during discovery, then code runs forever. Zero LLM calls at runtime.

How it works:
1. Point at any URL
2. Liquid tries MCP → OpenAPI → GraphQL → REST probing → Browser (Playwright) — cheapest first
3. AI proposes field mappings to your data model
4. Human reviews and approves
5. Deterministic sync runs on schedule — pure Python, no AI

What makes it different from Airbyte/Nango:
- No connector to write — discovery is automatic
- When the API changes, repair_adapter() diffs schemas and re-maps only broken fields
- Library, not platform — you control everything

Tech: Python 3.12+, Pydantic, httpx, AST-based transform evaluator (no eval). 210 tests. AGPL-3.0.

I borrowed pagination strategies from Airbyte CDK, auth management patterns from Nango, and OpenAPI parsing patterns from openapi-llm. Standing on the shoulders of giants.

Would love feedback on the architecture: https://github.com/ertad-family/liquid/blob/main/docs/ARCHITECTURE.md
```

**When to post:** Tuesday or Wednesday, 9-10 AM ET

---

## 2. Reddit r/Python

**Title:**
```
I built a Python library that uses AI to discover any API and generate typed adapters — then syncs data with zero LLM calls
```

**Body:**

```
I kept writing the same adapter pattern for every SaaS API: find endpoints, handle auth, map fields, sync data. For 50 services, that's 50 adapters.

So I built Liquid — a library where:
- AI discovers the API once (tries MCP → OpenAPI → GraphQL → REST → Browser)
- You review the field mappings
- Code syncs data forever — deterministic, zero LLM calls

The interesting bits:
- Progressive discovery: 70% of modern APIs have OpenAPI specs, so AI isn't even needed
- Safe transforms: field expressions like `value * -1` are AST-whitelisted, no eval()
- Auto-repair: when APIs change, it diffs schemas and re-maps only broken fields
- 4 pluggable pagination strategies, Protocol-based extension points

Tech: Python 3.12+, async-first, Pydantic models, 210 tests

GitHub: https://github.com/ertad-family/liquid

Feedback welcome. Also looking for contributors — 10 good first issues ready.
```

---

## 3. Reddit r/opensource

**Title:**
```
Liquid — open-source Python library for automatic API discovery and data sync (AGPL-3.0)
```

**Body:**

```
Just open-sourced Liquid, a Python library that solves the "50 APIs, 50 adapters" problem.

The approach: point at a URL, AI discovers the API structure, human approves field mappings, then deterministic code syncs data on schedule. AI is only involved during setup — runtime is pure Python with zero LLM dependency.

Why AGPL-3.0: I believe the discovery and mapping logic should remain open. Commercial licenses available for companies that can't do AGPL.

Built with: Python 3.12+, Pydantic, httpx. Protocol-based extension points — bring your own LLM, credential store, data sink.

https://github.com/ertad-family/liquid

Looking for early contributors and feedback on the architecture.
```

---

## 4. Reddit r/selfhosted

**Title:**
```
Liquid — self-hosted alternative to Nango/Airbyte for API integration (Python library, AGPL-3.0)
```

**Body:**

```
If you're self-hosting and need to pull data from multiple SaaS APIs, you've probably looked at Airbyte (heavy platform) or Nango (TypeScript-focused).

Liquid is a Python library (not a platform) that:
- Auto-discovers APIs — just give it a URL
- Handles auth classification (OAuth, API key, custom)
- Maps external fields to your data model with AI assistance
- Syncs data on schedule with zero AI dependency
- Auto-repairs when APIs change

It's a library, so you integrate it into your existing stack — no Docker compose, no separate service to manage.

https://github.com/ertad-family/liquid

Python 3.12+, 210 tests, AGPL-3.0.
```

---

## 5. Twitter/X Thread

```
Thread: I just open-sourced Liquid — a Python library where AI discovers APIs once, then code syncs data forever. No more writing adapters for every SaaS service.

1/ The problem: 50 APIs = 50 custom adapters. Each with unique endpoints, auth, pagination, data models. When Shopify renames a field, you debug at 2 AM.

2/ Liquid's approach: Point at a URL → AI discovers the API → Human verifies mapping → Deterministic sync runs forever. Zero LLM calls at runtime.

3/ Progressive discovery — tries cheapest method first:
• MCP server? → Done, no AI needed
• OpenAPI spec? → Parse it, no AI needed  
• GraphQL? → Introspection, no AI needed
• REST? → Probe + LLM
• No API? → Playwright captures traffic

4/ When APIs break your adapter, repair_adapter() diffs schemas and re-maps only the broken fields. Working mappings stay untouched. One call.

5/ Built with Python 3.12+, Pydantic, httpx. 210 tests. AGPL-3.0.

Borrowed patterns from Airbyte (pagination), Nango (auth), openapi-llm (parsing).

GitHub: https://github.com/ertad-family/liquid

⭐ Star it. 🐛 Break it. 🔀 Fork it.
```
