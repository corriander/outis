import json

import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request


def test_native_mode_is_the_default(monkeypatch):
    """Constructive-fork rule: the inherited Cookbook stays until provider-backed
    replacements reach parity, so an unconfigured deployment gets native."""
    monkeypatch.delenv("OUTIS_COOKBOOK_MODE", raising=False)

    from src.cookbook_capabilities import cookbook_capabilities

    document = cookbook_capabilities()

    assert document["mode"] == "native"
    assert document["capabilities"]["runtime_controller"]["start"] is True


def test_external_mode_is_catalogue_only(monkeypatch):
    monkeypatch.setenv("OUTIS_COOKBOOK_MODE", "external")

    from src.cookbook_capabilities import cookbook_capabilities

    document = cookbook_capabilities()

    assert document["mode"] == "external"
    assert document["capabilities"]["catalogue"] == {
        "provider": "huggingface",
        "browse": True,
        "inspect": True,
    }
    assert document["capabilities"]["artifact_store"]["acquire"] is False
    assert document["capabilities"]["profile_service"]["write"] is False
    assert document["capabilities"]["runtime_controller"]["start"] is False


def test_native_mode_preserves_upstream_operations(monkeypatch):
    monkeypatch.setenv("OUTIS_COOKBOOK_MODE", "native")

    from src.cookbook_capabilities import cookbook_capabilities

    capabilities = cookbook_capabilities()["capabilities"]

    assert capabilities["artifact_store"]["acquire"] is True
    assert capabilities["artifact_store"]["delete"] is True
    assert capabilities["profile_service"]["write"] is True
    assert capabilities["runtime_controller"]["start"] is True
    assert capabilities["runtime_controller"]["stop"] is True


def test_unsupported_native_operation_fails_closed(monkeypatch):
    monkeypatch.setenv("OUTIS_COOKBOOK_MODE", "external")

    from src.cookbook_capabilities import require_cookbook_capability

    with pytest.raises(HTTPException) as exc:
        require_cookbook_capability("runtime_controller", "start")

    assert exc.value.status_code == 501
    assert "runtime_controller.start" in exc.value.detail
    assert "OUTIS_COOKBOOK_MODE=native" in exc.value.detail


def test_capability_route_returns_backend_policy(monkeypatch):
    monkeypatch.setenv("OUTIS_COOKBOOK_MODE", "external")

    from routes.hwfit_routes import setup_hwfit_routes

    endpoint = next(
        route.endpoint
        for route in setup_hwfit_routes().routes
        if route.path == "/api/hwfit/capabilities"
    )

    assert endpoint()["capabilities"]["catalogue"]["browse"] is True
    assert endpoint()["capabilities"]["runtime_controller"]["start"] is False


def _request(path: str) -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [],
        "app": FastAPI(),
    })


def _json_request(path: str, payload: dict) -> Request:
    body = json.dumps(payload).encode("utf-8")

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request({
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [(b"content-type", b"application/json")],
        "app": FastAPI(),
    }, receive)


def _cookbook_endpoint(path: str, method: str):
    from routes.cookbook_routes import setup_cookbook_routes

    return next(
        route.endpoint
        for route in setup_cookbook_routes().routes
        if route.path == path and method in route.methods
    )


@pytest.mark.asyncio
async def test_external_download_route_never_reaches_native_runner(monkeypatch):
    from routes.cookbook_helpers import ModelDownloadRequest

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("OUTIS_COOKBOOK_MODE", "external")
    endpoint = _cookbook_endpoint("/api/model/download", "POST")

    with pytest.raises(HTTPException) as exc:
        await endpoint(_request("/api/model/download"), ModelDownloadRequest(repo_id="example/model"))

    assert exc.value.status_code == 501
    assert "artifact_store.acquire" in exc.value.detail


@pytest.mark.asyncio
async def test_external_serve_route_never_reaches_native_runner(monkeypatch):
    from routes.cookbook_helpers import ServeRequest

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("OUTIS_COOKBOOK_MODE", "external")
    endpoint = _cookbook_endpoint("/api/model/serve", "POST")

    with pytest.raises(HTTPException) as exc:
        await endpoint(
            _request("/api/model/serve"),
            ServeRequest(repo_id="example/model", cmd="vllm serve example/model"),
        )

    assert exc.value.status_code == 501
    assert "runtime_controller.start" in exc.value.detail


@pytest.mark.asyncio
async def test_external_dependency_install_never_reaches_host_runner(monkeypatch):
    from routes.shell_routes import setup_shell_routes

    monkeypatch.setenv("OUTIS_COOKBOOK_MODE", "external")
    endpoint = next(
        route.endpoint
        for route in setup_shell_routes().routes
        if route.path == "/api/cookbook/packages/install" and "POST" in route.methods
    )

    with pytest.raises(HTTPException) as exc:
        await endpoint(_request("/api/cookbook/packages/install"))

    assert exc.value.status_code == 501
    assert "runtime_controller.start" in exc.value.detail


@pytest.mark.parametrize("path", ["/api/hwfit/system", "/api/hwfit/models", "/api/hwfit/image-models"])
def test_external_hwfit_routes_never_probe_local_or_remote_hardware(monkeypatch, path):
    from routes.hwfit_routes import setup_hwfit_routes
    from services.hwfit import hardware

    monkeypatch.setenv("OUTIS_COOKBOOK_MODE", "external")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("hardware detection must not run in external mode")

    monkeypatch.setattr(hardware, "detect_system", fail_if_called)
    endpoint = next(route.endpoint for route in setup_hwfit_routes().routes if route.path == path)

    with pytest.raises(HTTPException) as exc:
        endpoint()

    assert exc.value.status_code == 501
    assert "runtime_controller.status" in exc.value.detail


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_external_cookbook_state_is_not_readable_or_writable(monkeypatch, tmp_path, method):
    import routes.cookbook_routes as cookbook_routes

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("OUTIS_COOKBOOK_MODE", "external")
    monkeypatch.setattr(cookbook_routes, "COOKBOOK_STATE_FILE", tmp_path / "cookbook_state.json")
    endpoint = _cookbook_endpoint("/api/cookbook/state", method)
    request = _json_request("/api/cookbook/state", {}) if method == "POST" else _request("/api/cookbook/state")

    with pytest.raises(HTTPException) as exc:
        await endpoint(request)

    assert exc.value.status_code == 501
    expected = "profile_service.write" if method == "POST" else "profile_service.read"
    assert expected in exc.value.detail


@pytest.mark.asyncio
async def test_external_cookbook_ssh_key_is_not_readable(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("OUTIS_COOKBOOK_MODE", "external")
    endpoint = _cookbook_endpoint("/api/cookbook/ssh-key", "GET")

    with pytest.raises(HTTPException) as exc:
        await endpoint(_request("/api/cookbook/ssh-key"))

    assert exc.value.status_code == 501
    assert "runtime_controller.status" in exc.value.detail


@pytest.mark.asyncio
async def test_external_tail_output_never_reaches_shell_exec(monkeypatch):
    from src.tools.cookbook import do_tail_serve_output

    monkeypatch.setenv("OUTIS_COOKBOOK_MODE", "external")
    result = await do_tail_serve_output('{"session_id":"serve-demo","remote_host":"example.invalid"}')

    assert result["exit_code"] == 1
    assert "runtime_controller.logs" in result["error"]
