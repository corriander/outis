"""Public project and corresponding-source metadata for Outis clients."""

from src.constants import (
    APP_VERSION,
    OUTIS_LICENSE,
    OUTIS_LICENSE_URL,
    OUTIS_PROJECT_NAME,
    OUTIS_UPSTREAM_NAME,
    outis_build_ref,
    outis_source_url,
)


def project_metadata() -> dict[str, str]:
    """Describe the running build for visible UI attribution and source links."""

    return {
        "version": APP_VERSION,
        "project": OUTIS_PROJECT_NAME,
        "upstream": OUTIS_UPSTREAM_NAME,
        "license": OUTIS_LICENSE,
        "license_url": OUTIS_LICENSE_URL,
        "source_url": outis_source_url(),
        "build_ref": outis_build_ref(),
    }
