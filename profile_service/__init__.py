"""Server-side adapter for an external ProfileService v1 provider.

Outis owns the profile editor; a conforming external service owns profile
schema, validation, path resolution, and persistence. This package holds the
provider-neutral HTTP client Outis speaks to any such service. The browser
never reaches the service directly and never sees its bearer token -- the
same-origin proxy in ``routes.profile_service_routes`` is the only caller.
"""
