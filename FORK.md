# Outis fork policy

Outis is a deliberately divergent, softly upstream-tracking fork of
[Odysseus](https://github.com/odysseus-dev/odysseus). The fork was established
on 19 July 2026 from the Odysseus v1.0.2 version-bump commit
`9844a2f9a1996b8c8135a9e7bbde6a72f41df5ed`.

## Why this fork exists

Odysseus provides a strong general-purpose local-AI workspace and an unusually
useful visual foundation. Outis develops that foundation as a client over
replaceable external resources and control planes.

The intended boundary is:

- Outis owns discovery, presentation, interaction, and user-editable client
  state;
- external services may own model artefacts, profiles, runtime lifecycle,
  knowledge stores, automation, and device control;
- integrations use explicit, documented interfaces rather than project-specific
  code embedded throughout the UI; and
- Outis does not become the mandatory serving authority for a model merely
  because it presents that model in the interface.

This is not a commitment to build a general extension marketplace or to turn
every feature into a plugin. Feature isolation and narrow provider boundaries
are the goal; a framework-wide rewrite is not.

## Branch and upstream model

- `main` is the default Outis product branch. Outis pull requests target it.
- `upstream/main` is the stable intake baseline for reviewing release-sized
  changes from Odysseus.
- `upstream/dev` is watched for relevant fixes and for work intended to be
  contributed back to Odysseus.
- Upstream changes are selected and reviewed; the upstream development branch
  is not merged wholesale into Outis.
- A generic upstream contribution should be prepared from the appropriate
  upstream branch and should not depend on Outis-only behavior.

Useful upstream changes may be cherry-picked or reimplemented when doing so
produces a clearer Outis architecture. Product coherence takes priority over a
low-diff fork, and compatibility with every future Odysseus release is not
promised.

## Public integration boundary

Outis integrations should describe roles such as a model catalogue, artefact
store, profile service, runtime controller, knowledge provider, or automation
service. A concrete implementation may satisfy one of those roles, but the
Outis-side contract must be understandable and implementable without access to
that implementation's private repository.

In particular:

- public examples and configuration use generic names and fictional values;
- interfaces include failure, availability, and capability behavior, not only
  the happy path;
- optional integrations degrade clearly when their provider is absent; and
- private source, internal paths, credentials, topology, and operational detail
  are not copied into this repository.

The first concrete boundary is the
[Cookbook capability contract](docs/cookbook-capabilities.md). The inherited
Cookbook remains the default (`native` mode); `external` mode declares a
provider-owned deployment and becomes the default only when provider-backed
capabilities reach parity with the inherited browser. Replacement is
constructive: working inherited surfaces are not removed to satisfy the
boundary.

## Public project record

The public issue and pull-request history is part of the project's published
documentation. Issues should be independently useful, appropriately scoped,
and free of inaccessible context. Exploratory notes do not need to become
public issues; when they do, they are rewritten as a self-contained problem,
decision, or implementation slice.

## Attribution and licensing

Original Odysseus copyright and attribution are retained. Outis modifications
are identified by this notice and the repository history. See
[LICENSING.md](LICENSING.md) for contribution, dependency, integration, and
network source-offer policy.
