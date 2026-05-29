# Bundled adapters (community, CC0)

Pre-discovered & pre-mapped adapters for public APIs, shipped in the wheel so they
work with **zero discovery and zero LLM**:

```python
from liquid import load_bundled_adapter, list_bundled_adapters

list_bundled_adapters()            # -> ["glama", ...]
adapter = load_bundled_adapter("glama")
data = await liquid.fetch(adapter)  # deterministic; no model call
```

Each `*.json` is the portable artifact Liquid produces — `{"target_model", "config"}` —
released into the **public domain (CC0, see `LICENSE`)**, independent of the AGPL code.

## Contribute one (PR)

1. Connect the API and get an `AdapterConfig`:
   `adapter = await liquid.get_or_create(url, target_model, auto_approve=True)`
2. Export it: `{"target_model": "...", "config": adapter.model_dump(by_alias=True, mode="json")}`.
3. **Scrub secrets** — set `auth_ref` to `"none"`, ensure no credentials in `source_url`,
   no tokens/keys anywhere. (CI rejects credential-like content.)
4. Confirm it fetches live with `llm=None`.
5. Save as `src/liquid/adapters/<service>.json` and open a PR.

**Only public / well-known APIs** — nothing private, internal, or auth-walled.
