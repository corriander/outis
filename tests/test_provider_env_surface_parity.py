"""Every provider variable must be wired everywhere a deployment reads it.

The provider configuration surface spans four files that nothing previously
tied together: three Compose files pass the variables into the container, and
`.env.schema` declares and validates them. The ProfileService variables reached
all three Compose files and `.env.example` but never `.env.schema`.

That gap did not block configuration -- varlock passes an undeclared variable
straight through, resolver functions included -- but an undeclared variable is
also an *unvalidated* one: a malformed URL or an out-of-range timeout reaches
the application instead of being refused at load. The Compose half of the
invariant is the one with teeth: a variable present in the default Compose file
and missing from a GPU one silently drops the capability for that deployment
shape.
"""

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILES = (
    "docker-compose.yml",
    "docker-compose.gpu-amd.yml",
    "docker-compose.gpu-nvidia.yml",
)

# The variables that select and configure an external provider. Build
# provenance (OUTIS_SOURCE_URL, OUTIS_BUILD_REF) is deliberately out of scope:
# it is set by the build, not chosen by an operator.
PROVIDER_VARIABLES = (
    "OUTIS_COOKBOOK_MODE",
    "OUTIS_ARTIFACT_STORE_URL",
    "OUTIS_ARTIFACT_STORE_NAME",
    "OUTIS_ARTIFACT_STORE_TOKEN",
    "OUTIS_ARTIFACT_STORE_TIMEOUT",
    "OUTIS_PROFILE_SERVICE_URL",
    "OUTIS_PROFILE_SERVICE_NAME",
    "OUTIS_PROFILE_SERVICE_TOKEN",
    "OUTIS_PROFILE_SERVICE_TIMEOUT",
)


def _schema_declarations() -> dict[str, str]:
    text = (ROOT / ".env.schema").read_text(encoding="utf-8")
    return {
        match.group(1): match.group(2)
        for match in re.finditer(r"^([A-Z][A-Z0-9_]*)=(.*)$", text, re.MULTILINE)
    }


@pytest.mark.parametrize("variable", PROVIDER_VARIABLES)
def test_every_provider_variable_is_declared_in_the_schema(variable):
    assert variable in _schema_declarations(), (
        f"{variable} is passed into the container but never declared in "
        ".env.schema, so its value reaches the application unvalidated."
    )


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
@pytest.mark.parametrize("variable", PROVIDER_VARIABLES)
def test_every_provider_variable_reaches_every_compose_file(compose_file, variable):
    text = (ROOT / compose_file).read_text(encoding="utf-8")
    assert f"{variable}=${{{variable}" in text, (
        f"{variable} is not passed to the app service in {compose_file}, so "
        "that deployment shape silently loses the provider."
    )


@pytest.mark.parametrize(
    "variable",
    ("OUTIS_ARTIFACT_STORE_TOKEN", "OUTIS_PROFILE_SERVICE_TOKEN"),
)
def test_provider_tokens_are_declared_sensitive_and_never_carry_a_value(variable):
    text = (ROOT / ".env.schema").read_text(encoding="utf-8")
    block = text.split(f"{variable}=")
    assert len(block) == 2, f"{variable} is not declared exactly once"
    annotations, remainder = block[0], block[1]
    # varlock infers sensitivity for these anyway; the annotation states it
    # rather than depending on that inference holding.
    assert "@sensitive" in annotations.rsplit("\n\n", 1)[-1], (
        f"{variable} must be annotated @sensitive."
    )
    # The load-bearing half: a bearer token must never be committed, and the
    # schema is a committed file.
    assert remainder.split("\n", 1)[0].strip() == "", (
        f"{variable} must have no value in the committed schema."
    )
