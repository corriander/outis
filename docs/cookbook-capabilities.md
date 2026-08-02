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
or runtime operations. Configuring a ProfileService adds provider-owned profile
authoring through a same-origin proxy without enabling acquisition or runtime
operations. Inventory and profile providers are selected independently; neither
implies the other, and an external ProfileService never re-enables the inherited
local profile routes. External mode becomes the intended default only when
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
`observed.split` object with `parts_present` and, when the provider can
determine it, `parts_expected`. `parts_expected` is **optional**: a provider
omits it when the observed parts disagree about the total, so no single expected
count exists. Clients must render that absence as unknown rather than as zero.

A source entry in `status.sources` may carry an optional `error` string
explaining why it is not `ready`. Outis displays it verbatim and never parses
it. Clients must treat unknown additive fields as optional so provenance can be
added later without making it a prerequisite for inventory.

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

The browser treats variant labels as provider-defined display text rather than
an operating-system enum: `WSL`, `Win11`, remote-shell contexts, and future
labels all use the same rendering and copy behavior. Contract normalization and
the pure inventory view model live in `frontend/cookbookInventoryModel.ts`;
`npm run build:inventory` emits the committed no-build-browser module consumed
by the handwritten DOM adapter.

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

## External runtime profiles

Set `OUTIS_PROFILE_SERVICE_URL` to let the Cookbook author runtime profiles
against an external ProfileService that implements the version 1 profile
contract. The service owns the profile schema, validation, path resolution, and
persistence; Outis owns only the editor. Configuration mirrors the inventory
client, including the environment variables below:

- `OUTIS_PROFILE_SERVICE_URL` — absolute HTTP(S) base URL of the service. It is
  validated and normalised; credentials, query, and fragment are rejected.
- `OUTIS_PROFILE_SERVICE_TOKEN` — server-side bearer token. It is attached to
  every upstream request and is **never** placed in a browser response body,
  header, cookie, or local storage.
- `OUTIS_PROFILE_SERVICE_NAME` — optional display name shown next to the
  provider; defaults to `external-profile-service`.
- `OUTIS_PROFILE_SERVICE_TIMEOUT` — per-request timeout in seconds, clamped to
  `[0.5, 60]` (default 10).

These variables are one of two alternative sources. A deployment manager can
instead supply the same values once through the managed one-shot bootstrap,
which persists them encrypted; see
[Managed one-shot bootstrap](setup.md#managed-one-shot-bootstrap). A persisted
configuration is authoritative **as a whole** — Outis never combines its fields
with environment values, so a stale `OUTIS_PROFILE_SERVICE_URL` left in the
environment cannot partially override a bootstrapped service. The variables
above apply whenever no persisted ProfileService state exists.

The ProfileService and ArtifactStore roles are selected independently. Pointing
both at the same endpoint is a legitimate deployment choice, but configuring
one never configures the other.

Both directions through the proxy are size-capped at 5 MiB, enforced
incrementally while the body streams so an oversize or length-lying message is
abandoned rather than buffered first. An oversize browser request is refused
with `413 request_too_large`; an oversize upstream response becomes
`502 profile_service_invalid`.

Outis reaches the service server-side only, with redirects disabled and ambient
proxy environment ignored. A same-origin proxy under
`/api/cookbook/profile-service` exposes the contract to the browser behind the
existing admin/session authority:

```text
GET    /api/cookbook/profile-service                 # discovery document (+ provider_name)
GET    /api/cookbook/profile-service/form            # fields[] form vocabulary
POST   /api/cookbook/profile-service/draft           # seed a stateless draft from an ArtifactRef
POST   /api/cookbook/profile-service/preview         # validate values without persisting
GET    /api/cookbook/profile-service/profiles        # list
POST   /api/cookbook/profile-service/profiles        # create
GET    /api/cookbook/profile-service/profiles/{id}   # read (surfaces ETag)
PUT    /api/cookbook/profile-service/profiles/{id}   # replace (requires If-Match)
PATCH  /api/cookbook/profile-service/profiles/{id}   # partial update with explicit clear (requires If-Match)
DELETE /api/cookbook/profile-service/profiles/{id}   # delete (requires If-Match)
```

The proxy preserves the provider's structured `{errors, warnings}` envelope and
its status codes so the editor can render field- and profile-level feedback. The
one translation is authentication: an upstream 401 means the server-side token
is wrong, so the proxy returns a 502 `profile_service_unauthorized` rather than a
browser 401. An unreachable service is 502 `profile_service_unreachable`; a
response that violates the contract is 502 `profile_service_invalid`.

Two boundaries are load-bearing:

- **Concurrency is explicit.** Read and create responses carry an `ETag`; writes
  require the browser to send the matching `If-Match`. The proxy forwards that
  header verbatim and never synthesises a wildcard overwrite — a missing
  precondition is the service's `428` to answer.
- **Requests carry an ArtifactRef, never a model path.** A draft or create
  request identifies an artifact by `{authority, artifact_id, observation}`
  only; any concrete path a caller attaches is dropped before the request
  leaves Outis. The service resolves the launch path itself. Exact inventory
  path variants remain display/copy metadata and are never submitted.

The set of artifact authorities a service accepts is advertised by its discovery
document (`accepted_authorities`), which the browser reads through the proxy. The
synchronous capability document does not enumerate them: it reports only that an
external ProfileService is configured (`profile_service.external`) and its
display provider, because honest enumeration requires a live call.

## The profile authoring island

A configured ProfileService adds a **Profiles** tab to the Cookbook. The tab is
keyed on `profile_service.external`, never on `read`/`write`: those two gate the
inherited host-side profile routes and stay native-only, so an external provider
can never switch on the local-file implementation.

Outis owns the authoring experience and nothing else. Every control in the tab
is derived from the service's own form document, so **no profile field is named
anywhere in Outis** — not in the browser module, not in its model, not in this
document. The form vocabulary the editor understands is provider-neutral:

- `groups[]` — `{id, label, order}` sections, rendered in the provider's order.
- `fields[]` — `{id, label, kind, widget, group, order, help, default, nullable}`
  plus optional `constraints` (`min`, `max`, `pattern`), `allowed` for a closed
  set, and `item` for the element type of a list.
- `widget_fallbacks` — a `kind`-to-`widget` map for fields that omit `widget`.

Recognised widgets are `text`, `number`, `toggle`, `select`, and `chips`. An
unrecognised widget, kind, or group is **rendered anyway** — as a text control in
a trailing section — rather than dropped. Dropping it would hide a value that a
replace still submits, and a replace omits nothing.

That is the central constraint on the editor: **replace is lossy by design**. The
service clears any key a `PUT` omits, so the editor always submits a complete
values object, including untouched fields and including any key present in the
loaded profile that its copy of the form does not declare. A provider that gains
a field after the browser fetched the form must not have it wiped by an editor
that never knew about it.

Validation stays with the service. The editor debounces a `preview` call while
the user types and renders the returned field- and profile-level messages
against the pointers the service filed them under (`/values/<field>` addresses a
field; `/`, `/artifact_ref`, and `/artifact_ref/<key>` are profile-level). It
also derives advisory local hints from the provider's own `constraints`, but
those never gate submission — duplicating provider rules in the browser would
only let the two drift.

Three states are handled as first-class outcomes rather than errors:

- **Validation failure.** The draft is untouched; the service's messages appear
  against their fields.
- **Provider outage.** The draft is untouched and stays editable; the failure is
  reported on the panel and nowhere else in the Cookbook.
- **Stale edit.** A `412` never discards the draft. The editor re-reads the
  profile, shows the conflict alongside the user's version, and offers an
  explicit choice: adopt the provider's version, or keep the local draft and
  overwrite at the version just read. Nothing is written until the user picks.

The inventory tab's `cookbook:artifact-selected` event is the hand-off between
the two islands. Selecting an artifact only *records* it as context; seeding a
draft from it is a separate, explicit action, so browsing the inventory can
never discard an in-progress draft. The selected artifact is submitted as an
`{authority, artifact_id, observation}` reference — the same projection the
server-side client enforces — and the editor warns, without blocking, when the
service's `accepted_authorities` does not list that authority.

Contract normalization, the form vocabulary, and the editor state machine live
in `frontend/cookbookProfileEditorModel.ts`; `npm run build:profiles` emits the
committed no-build-browser module consumed by the handwritten DOM adapter in
`static/js/cookbookProfiles.js`. Deleting profiles is deliberately not in this
slice.

## Boundary scope

This policy governs Cookbook-specific HTTP routes, frontend controls, and agent
tools. It is not an agent sandbox: a separately authorised generic shell or
administrative API remains a distinct privileged surface and can operate on the
host independently of Cookbook capabilities.

## Frontend evolution boundary

The inherited Cookbook frontend is imperative browser JavaScript concentrated
in `static/js/cookbook.js`. Existing Launch, Download, Dependencies, and
Settings behavior remains supported, but substantial provider-backed authoring
must not extend that module into the source of truth for profiles.

Profile authoring accordingly enters as an isolated TypeScript frontend module,
with its own build and test boundary, over the `ArtifactStore` and
`ProfileService` contracts. Validation and persistence belong to the service;
the module owns the editable draft, its version handle, and the mapping from the
provider's form vocabulary onto controls. INI files and other runtime formats
are projections the service maintains rather than records the browser duplicates
and edits directly. Local inventory, remote catalogue results, authored
profiles, and runtime state may later share presentation components, but remain
distinct domain objects.

This boundary does not require rewriting the inherited Cookbook before useful
provider-backed slices can ship. Read-only inventory can remain a separate
working tab while the typed authoring surface and its final navigation are
designed.

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
