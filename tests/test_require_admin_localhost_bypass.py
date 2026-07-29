"""require_admin must honour LOCALHOST_BYPASS the way require_user already does.

LOCALHOST_BYPASS=true is the documented dev switch: AuthMiddleware lets a
direct loopback request through without a session cookie, and
``auth_helpers.require_user`` mirrors that so owner-scoped routes don't 401 a
caller the middleware just admitted (pinned by
``test_require_user_localhost_bypass_admits_loopback``).

``require_admin`` was never given the same treatment. It reads
``request.state.current_user``, which the bypass path never sets, so every
admin-gated route answered 403 "Admin only" while the rest of the app was
browsable. The bypass was therefore only good enough to load the UI, not to
exercise it — GET /api/cookbook/artifacts, the external inventory route, was
unreachable under the very switch that exists to make local testing possible.

The loopback test here is deliberately STRICTER than require_user's: it uses
the same direct-connection-plus-no-forwarding-headers rule the middleware
applies, so a request arriving through a Cloudflare tunnel or reverse proxy
(which connects from 127.0.0.1) can never inherit admin.
"""

import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from core.middleware import require_admin


class _Mgr:
    is_configured = True

    def is_admin(self, user):
        return False


def _request(client=("127.0.0.1", 51234), headers=None):
    app = FastAPI()
    app.state.auth_manager = _Mgr()
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/api/cookbook/artifacts",
        "headers": [
            (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
        ],
        "app": app,
        "client": client,
    })


def test_bypass_admits_direct_loopback(monkeypatch):
    """The case that motivated this: loopback + bypass reaches admin routes."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("LOCALHOST_BYPASS", "true")

    require_admin(_request())  # must not raise


def test_bypass_does_not_admit_lan_caller(monkeypatch):
    """A LAN visitor still authenticates, exactly as for require_user."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("LOCALHOST_BYPASS", "true")

    with pytest.raises(HTTPException) as exc:
        require_admin(_request(client=("192.168.1.50", 51234)))
    assert exc.value.status_code == 403


@pytest.mark.parametrize(
    "header",
    ["x-forwarded-for", "cf-connecting-ip", "x-real-ip", "forwarded"],
)
def test_bypass_does_not_admit_proxied_request(monkeypatch, header):
    """cloudflared/nginx connect FROM 127.0.0.1, so a bare host check would
    hand a remote visitor admin. Forwarding headers must disqualify."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("LOCALHOST_BYPASS", "true")

    with pytest.raises(HTTPException) as exc:
        require_admin(_request(headers={header: "203.0.113.7"}))
    assert exc.value.status_code == 403


def test_loopback_without_bypass_still_refused(monkeypatch):
    """Loopback alone confers nothing — the operator must opt in."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("LOCALHOST_BYPASS", "false")

    with pytest.raises(HTTPException) as exc:
        require_admin(_request())
    assert exc.value.status_code == 403
