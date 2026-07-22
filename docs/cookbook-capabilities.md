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
When a deployment mixes providers within one group, `operation_providers`
identifies the provider for each operation. For example, native acquisition can
remain available while inventory enumeration comes from an external store.
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
providers configured, external mode is a deliberately reduced catalogue-only
surface (browse and broad search; operational routes 501). Configuring an
ArtifactStore adds read-only inventory without enabling acquisition, profile,
or runtime operations. External mode becomes the intended default only when
provider-backed capabilities reach parity with the inherited browser. Future
providers should implement one or more capability groups without making
unrelated groups appear available.

Inherited hardware-fit routes are part of `runtime_controller.status` because
they inspect a prospective execution host and may invoke SSH. They are not
available in external mode.

## External GGUF inventory

Set `OUTIS_ARTIFACT_STORE_URL` to make the Cookbook's **Inventory** tab consume
an external ArtifactStore. Outis calls:

```text
GET <provider>/v1/artifacts
```

The version 1 inventory envelope contains a provider authority, provider/source
status, and artifacts with an authority-scoped stable ID, source ID, logical
relative path, observation token, observed filename, size, modification time,
format, quantisation when it can be derived from the filename, and readiness
state. The first format adapter is intentionally GGUF-only. It groups complete
split GGUF sets into one artifact and reports missing or temporary parts as
incomplete. Other files and model stores, provenance, remote-catalogue matching,
acquisition, and mutation are outside this first slice.

An example envelope is:

```json
{
  "schema_version": 1,
  "provider": {
    "id": "inventory-example-a",
    "name": "Directory inventory",
    "class": "directory"
  },
  "status": {
    "state": "ready",
    "observed_at": "2026-01-01T12:00:00Z",
    "sources": [
      {"id": "models", "label": "Local models", "state": "ready", "artifact_count": 1}
    ]
  },
  "artifacts": [
    {
      "id": "models:family/example-Q4_K_M.gguf:0123456789ab",
      "source_id": "models",
      "observation": "0123456789abcdef",
      "filename": "example-Q4_K_M.gguf",
      "logical_path": "family/example-Q4_K_M.gguf",
      "display_location": "Local models / family / example-Q4_K_M.gguf",
      "group_path": ["family"],
      "path_variants": [
        {"label": "Runtime host", "value": "/srv/models/family/example-Q4_K_M.gguf"}
      ],
      "observed": {
        "size_bytes": 4294967296,
        "modified_at": "2026-01-01T11:00:00Z",
        "format": "gguf",
        "quantization": "Q4_K_M",
        "state": "ready"
      },
      "files": [
        {
          "filename": "example-Q4_K_M.gguf",
          "size_bytes": 4294967296,
          "modified_at": "2026-01-01T11:00:00Z"
        }
      ]
    }
  ]
}
```

`status.state` is `ready`, `partial`, or `unreachable`; an artifact's
`observed.state` is `ready` or `incomplete`. Split artifacts also include an
`observed.split` object with `parts_present` and `parts_expected`. Clients must
treat unknown additive fields as optional so provenance can be added later
without making it a prerequisite for inventory.

`provider.id` is a stable authority for one configured provider instance;
`provider.class`, when present, is only an implementation hint. An ArtifactRef
is `{authority: provider.id, artifact_id: artifact.id}` and treats the artifact
ID as opaque. `source_id` identifies the provider-owned source without requiring
a client to parse the artifact ID. The stable identity remains unchanged when a
source label changes. `observation` is separate and changes when the provider
observes a replacement at that identity.

`logical_path` is the structured, `/`-separated path used for ordinary browsing.
`display_location` may add a source label. A provider may also publish zero or
more `path_variants` as `{label, value}` pairs. Outis displays and copies each
value exactly as supplied, but never parses, translates, joins, reconstructs, or
submits it as identity. A provider that omits path variants remains fully usable.
By default the reference provider does not expose its scan root; configuring a
path variant is an explicit operator choice. A profile service resolves an
ArtifactRef in its own filesystem namespace.

This repository includes a small reference provider implemented with the
Python standard library. A WSL-hosted example is:

```bash
export OUTIS_ARTIFACT_PROVIDER_TOKEN='replace-with-a-long-random-value'
python -m artifact_store.directory_provider \
  --root models=/mnt/d/models \
  --label 'models=Local models' \
  --provider-id inventory-example-a \
  --path-variant 'models:Runtime host=/srv/models/{rel}' \
  --bind 0.0.0.0 \
  --port 7331
```

Use the same secret as `OUTIS_ARTIFACT_STORE_TOKEN` in Outis and configure the
URL reachable from the Outis container, commonly
`http://host.docker.internal:7331`. The provider binds to loopback by default
and refuses a non-loopback bind unless `OUTIS_ARTIFACT_PROVIDER_TOKEN` is set.
Multiple `--root ID=PATH`, `--label ID=LABEL`, and
`--path-variant ROOT_ID:LABEL=TEMPLATE` options are supported. Variant templates
may use `{path}` for the observed host path, `{root}` for the configured root,
and `{rel}` for the logical relative path. Root IDs and provider IDs must remain
stable because together they establish ArtifactRef identity; labels may be
changed without changing IDs.

The command-line provider deliberately has no default authority:
`--provider-id` is required so two independently configured instances cannot
silently publish the same ArtifactRef namespace. Direct Python callers of
`inventory_document()` retain the historical `directory` default for source
compatibility only; network providers must not rely on it.

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
