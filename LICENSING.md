# Outis licensing policy

Outis is distributed under the GNU Affero General Public License,
version 3 or (at your option) any later version (`AGPL-3.0-or-later`). The
complete terms are in [LICENSE](LICENSE).

This document records the project's operating policy. It is not a substitute
for the licence text or for legal advice about a particular distribution or
deployment.

## Fork and contributions

- Outis retains the copyright and attribution notices inherited from Odysseus
  and its other sources.
- Outis modifications are recorded by [FORK.md](FORK.md) and Git history.
- Contributions submitted to this repository must be compatible with
  `AGPL-3.0-or-later` and are accepted under that project licence.
- Third-party code and assets retain their own notices. See
  [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md) and [`licenses/`](licenses/).

## Network source offer

The AGPL requires a modified program used interactively over a network to offer
its corresponding source to those users. Outis treats that offer as a product
surface, not a deployment footnote:

- the login page and authenticated sidebar show **Source** and licence links;
- `OUTIS_SOURCE_URL` configures the source link and defaults to the public Outis
  repository;
- `OUTIS_BUILD_REF` may display a release tag or commit identifier; and
- an operator deploying a modified version should set `OUTIS_SOURCE_URL` to the
  public tag, commit, or archive containing the corresponding source for the
  version actually deployed.

Only HTTP and HTTPS source URLs without embedded credentials are accepted by
the application. An invalid value falls back to the canonical Outis repository.

## External services and private code

An HTTP, CLI, or filesystem boundary is an architectural boundary; it is not by
itself a licence firewall. Each distributor and operator remains responsible
for assessing the licences and coupling of the complete system they ship.

Outis follows these repository rules:

- private implementation code is not copied into Outis;
- public integration contracts must stand on their own and use generic terms;
- code contributed to Outis must be suitable for publication under the project
  licence; and
- credentials, private infrastructure details, and proprietary data remain
  outside the repository and are supplied through documented configuration.

These rules protect both the public usefulness of Outis and the confidentiality
of systems that happen to integrate with it.
