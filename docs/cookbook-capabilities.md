# Cookbook capability boundary

The Cookbook is a client over replaceable model services. It must not infer
whether acquisition, profile, or runtime operations are available from visible
buttons, saved browser state, detected hardware, or the presence of host tools.

The backend publishes the deployment policy at:

```text
GET /api/hwfit/capabilities
```

The version 1 document contains four independently meaningful groups:

- `catalogue`: browse and inspect model metadata;
- `artifact_store`: enumerate, acquire, and delete model artifacts;
- `profile_service`: read and write editable runtime profiles; and
- `runtime_controller`: query status, start or stop a runtime, and read logs.

Each group names its provider, if any, and exposes operation-level booleans.
Backend enforcement is authoritative: operations whose capability is absent
return HTTP 501 instead of falling through to the inherited local, SSH, tmux,
PowerShell, or container implementations. The frontend keeps rendering the
full inherited interface — Outis replaces parts constructively as enhanced
versions arrive; it does not remove working surfaces to satisfy the boundary.

## Deployment modes

Outis defaults to `native` mode: the complete inherited Odysseus Cookbook.
The fork's operating rule is that an imperfect inherited feature stays until
its provider-backed replacement is at least as useful — operators simply avoid
the parts they don't want (e.g. serving on a node whose lifecycle is owned
elsewhere) until the replacement lands.

`OUTIS_COOKBOOK_MODE=external` declares a deployment where artifact storage,
profiles, and runtime lifecycle are owned by external providers. With no
providers implemented yet, external mode is a deliberately reduced
catalogue-only surface (browse and broad search; operational routes 501) and
is not the recommended configuration. It becomes the intended default only
when provider-backed capabilities reach parity with the inherited browser.
Future providers should implement one or more capability groups without making
unrelated groups appear available.

Inherited hardware-fit routes are part of `runtime_controller.status` because
they inspect a prospective execution host and may invoke SSH. They are not
available in external mode.

## Boundary scope

This policy governs Cookbook-specific HTTP routes, frontend controls, and agent
tools. It is not an agent sandbox: a separately authorised generic shell or
administrative API remains a distinct privileged surface and can operate on the
host independently of Cookbook capabilities.

## Broad Hugging Face discovery

Explicit text searches use:

```text
GET /api/hwfit/discover?query=<terms>&limit=50
```

The endpoint preserves Hugging Face search results, including adapters,
finetunes, repositories with incomplete metadata, and artifacts that the native
runtime cannot serve. It does not send runtime, architecture, quantisation,
context, or hardware filters to Hugging Face. Zero-download/zero-like repos
are dropped by default as a stated filter (`hidden_count` reports how many);
`show_all=true` includes them.

In **native mode**, each hit whose name (or Hub safetensors metadata) yields a
parameter count is enriched through the same name-heuristic estimation and
fit-scoring path as the dynamic catalogue entries in
`services/hwfit/hf_discovery.py` — a search hit is a candidate catalogue entry
that hasn't been enriched, not a separate row species. The route accepts the
same hardware-override parameters as `/api/hwfit/models` so search rows rank
against the identical (possibly manual) profile. Hits with no parameter
estimate stay raw with fit `unknown`. Because enrichment ranks against local
hardware, it is part of the `runtime_controller.status` capability: in
**external mode** every hit stays raw and unassessed.

In the browser, broad discovery is opt-in via the search-type dropdown's
**Extended** option; Standard and Vision issue no `/discover` call and behave
exactly as the inherited browser. Extended results merge into the same list as
curated-catalogue and Ollama-library rows, tagged `HF+`, with estimates
visually attributed as estimates — an unassessed row is never ranked as if
assessed. Rendering both sources side by side is deliberate: it keeps the
inherited browser fully usable and makes the broad-search delta directly
visible and comparable per query.

Responses keep relevance and compatibility separate:

- `relevance` records the catalogue source, query, and ordering
  (`downloads` — the plain Hub API offers no true relevance sort, and its
  unsorted default is arbitrary match order);
- `compatibility.status` is `unknown` until a provider evaluates a selected
  artifact (name-heuristic enrichment estimates fit; it does not assess
  serveability); and
- `compatibility.annotations` reports material metadata such as a likely
  adapter, missing pipeline information, or gated access without treating an
  inference as fact.

When Hugging Face supplies another page, `next_cursor` can be passed back as the
`cursor` query parameter. The backend accepts only the opaque cursor value and
reconstructs the Hugging Face URL itself.
