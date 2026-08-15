"""HTTP client for a provider-neutral profile-set RuntimeController."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from runtime_controller.config import (
    DEFAULT_PROVIDER_NAME,
    RuntimeControllerConfiguration,
    RuntimeControllerConfigurationError,
    persisted_configuration_present,
    resolve_runtime_controller_configuration,
    validated_base_url,
)

MAX_RUNTIME_CONTROLLER_BYTES = 5 * 1024 * 1024


class RuntimeControllerError(RuntimeError):
    """The configured controller cannot be used as configured."""


class RuntimeControllerUnavailable(RuntimeControllerError):
    """The controller could not be reached."""


class RuntimeControllerUnauthorized(RuntimeControllerError):
    """The controller rejected Outis's server-side bearer token."""


class RuntimeControllerInvalid(RuntimeControllerError):
    """The controller returned a response outside the supported contract."""


class _WireModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class _WireError(_WireModel):
    pointer: str
    code: str
    message: str


class _WireWarning(_WireModel):
    code: str
    message: str


class _ErrorEnvelope(_WireModel):
    errors: list[_WireError] = Field(min_length=1)
    warnings: list[_WireWarning] = Field(default_factory=list)


class _RestartData(_WireModel):
    target: str
    state: str
    configuration: str
    verification: str
    evicted: list[str]
    report: dict[str, Any]


class _RestartEnvelope(_WireModel):
    data: _RestartData
    warnings: list[_WireWarning] = Field(default_factory=list)


class _RestartAdvertisement(_WireModel):
    method: str
    url_template: str
    allow_eviction_default: bool


class _DevicesAdvertisement(_WireModel):
    restart: _RestartAdvertisement


class _ServiceDocument(_WireModel):
    contract_version: int
    devices: _DevicesAdvertisement


def _validate(body: Any, model: type[BaseModel], what: str) -> None:
    try:
        model.model_validate(body)
    except ValidationError as exc:
        raise RuntimeControllerInvalid(
            f"RuntimeController returned a malformed {what} response"
        ) from exc


class RuntimeControllerResponse:
    __slots__ = ("status_code", "body")

    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self.body = body


def configured_runtime_controller_name() -> str:
    try:
        configuration = resolve_runtime_controller_configuration()
    except RuntimeControllerConfigurationError:
        return DEFAULT_PROVIDER_NAME
    return configuration.name if configuration else DEFAULT_PROVIDER_NAME


def runtime_controller_configured() -> bool:
    try:
        return resolve_runtime_controller_configuration() is not None
    except RuntimeControllerConfigurationError:
        return persisted_configuration_present()


class RuntimeControllerClient:
    def __init__(
        self,
        base_url: str,
        target: str,
        token: str | None = None,
        timeout_seconds: float = 60.0,
        name: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        try:
            self.base_url = validated_base_url(base_url)
        except RuntimeControllerConfigurationError as exc:
            raise RuntimeControllerError(str(exc)) from exc
        self.target = target
        self.token = token.strip() if token else None
        self.timeout_seconds = timeout_seconds
        self.name = (name.strip() if name else None) or configured_runtime_controller_name()
        self._transport = transport

    @classmethod
    def from_configuration(
        cls,
        configuration: RuntimeControllerConfiguration | None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> RuntimeControllerClient | None:
        if configuration is None:
            return None
        return cls(
            configuration.base_url,
            configuration.target,
            token=configuration.token,
            timeout_seconds=configuration.timeout_seconds,
            name=configuration.name,
            transport=transport,
        )

    @classmethod
    def from_config(cls) -> RuntimeControllerClient | None:
        try:
            return cls.from_configuration(resolve_runtime_controller_configuration())
        except RuntimeControllerConfigurationError as exc:
            raise RuntimeControllerError(str(exc)) from exc

    async def _read_capped(self, response: httpx.Response) -> bytes:
        declared = response.headers.get("Content-Length")
        if declared is not None:
            try:
                if int(declared) > MAX_RUNTIME_CONTROLLER_BYTES:
                    raise RuntimeControllerInvalid(
                        "RuntimeController response exceeds the size limit"
                    )
            except ValueError:
                pass
        total = 0
        chunks: list[bytes] = []
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > MAX_RUNTIME_CONTROLLER_BYTES:
                raise RuntimeControllerInvalid("RuntimeController response exceeds the size limit")
            chunks.append(chunk)
        return b"".join(chunks)

    async def _request(
        self, method: str, path: str, *, json_body: dict | None = None
    ) -> RuntimeControllerResponse:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=False,
                trust_env=False,
                transport=self._transport,
            ) as client:
                async with client.stream(
                    method,
                    f"{self.base_url}{path}",
                    headers=headers,
                    json=json_body,
                ) as response:
                    if response.status_code == 401:
                        raise RuntimeControllerUnauthorized(
                            "The configured RuntimeController rejected the server-side token"
                        )
                    if 300 <= response.status_code < 400:
                        raise RuntimeControllerInvalid(
                            "RuntimeController returned an unexpected redirect"
                        )
                    content = await self._read_capped(response)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise RuntimeControllerUnavailable("RuntimeController provider is unreachable") from exc
        except httpx.HTTPError as exc:
            raise RuntimeControllerUnavailable("RuntimeController provider request failed") from exc
        try:
            body = json.loads(content)
        except ValueError as exc:
            raise RuntimeControllerInvalid("RuntimeController returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise RuntimeControllerInvalid("RuntimeController returned a non-object body")
        return RuntimeControllerResponse(response.status_code, body)

    async def get_service(self) -> RuntimeControllerResponse:
        response = await self._request("GET", "/v1/service")
        if response.status_code != 200:
            raise RuntimeControllerInvalid("RuntimeController discovery returned no document")
        _validate(response.body, _ServiceDocument, "discovery")
        restart = response.body["devices"]["restart"]
        if (
            response.body.get("contract_version") != 1
            or restart.get("method") != "POST"
            or restart.get("url_template") != "/v1/devices/{noun}/restart"
            or restart.get("allow_eviction_default") is not False
        ):
            raise RuntimeControllerInvalid(
                "RuntimeController does not advertise the supported restart operation"
            )
        return response

    async def apply_profiles(self, *, allow_eviction: bool = False) -> RuntimeControllerResponse:
        target = quote(self.target, safe="")
        response = await self._request(
            "POST",
            f"/v1/devices/{target}/restart",
            json_body={"allow_eviction": allow_eviction},
        )
        if response.status_code == 200:
            _validate(response.body, _RestartEnvelope, "restart")
        else:
            _validate(response.body, _ErrorEnvelope, "restart error")
        return response
