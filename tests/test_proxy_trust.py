"""Forwarding headers are trusted only in the configured deployment mode."""

from pathlib import Path

import pytest
from flask import request, url_for
from init import create_app
from models import db
from werkzeug.middleware.proxy_fix import ProxyFix


def configured_app(monkeypatch, hops="0", hosts="hangar.example,localhost,127.0.0.1"):
    monkeypatch.setenv("OPENHANGAR_PROXY_HOPS", hops)
    monkeypatch.setenv("OPENHANGAR_TRUSTED_HOSTS", hosts)
    application = create_app()
    application.config.update(TESTING=True, RATELIMIT_ENABLED=False)
    with application.app_context():
        db.create_all()

    # Record what the request looked like instead of echoing it back: the test
    # reads it from here, and the route never reflects client-supplied values.
    seen = application.extensions["proxy_probe"] = []

    @application.route("/proxy-test")
    def probe():
        seen.append(
            {
                "address": request.remote_addr,
                "scheme": request.scheme,
                "host": request.host,
                "external": url_for("auth.login", _external=True),
            }
        )
        return "", 204

    return application


def observed(application):
    """What the most recent /proxy-test request looked like to the app."""
    return application.extensions["proxy_probe"][-1]


def test_direct_mode_ignores_all_forwarding_headers(monkeypatch):
    application = configured_app(monkeypatch)
    assert not isinstance(application.wsgi_app, ProxyFix)
    response = application.test_client().get(
        "/proxy-test",
        base_url="http://hangar.example:8087",
        environ_overrides={"REMOTE_ADDR": "192.0.2.9"},
        headers={
            "X-Forwarded-For": "127.0.0.1",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "attacker.invalid",
            "X-Forwarded-Port": "443",
            "X-Forwarded-Prefix": "/evil",
            "Forwarded": "for=127.0.0.1;host=attacker.invalid",
        },
    )
    assert response.status_code == 204
    assert observed(application) == {
        "address": "192.0.2.9",
        "scheme": "http",
        "host": "hangar.example:8087",
        "external": "http://hangar.example:8087/login",
    }


@pytest.mark.parametrize("hops, expected", [("1", "192.0.2.8"), ("2", "198.51.100.4")])
def test_proxy_mode_uses_configured_hops_and_public_origin(monkeypatch, hops, expected):
    application = configured_app(monkeypatch, hops)
    response = application.test_client().get(
        "/proxy-test",
        headers={
            "X-Forwarded-For": "198.51.100.4, 192.0.2.8",
            "X-Forwarded-Proto": "https, https",
            "X-Forwarded-Host": "hangar.example, hangar.example",
        },
        environ_overrides={"REMOTE_ADDR": "172.20.0.3"},
    )
    assert response.status_code == 204
    assert observed(application) == {
        "address": expected,
        "scheme": "https",
        "host": "hangar.example",
        "external": "https://hangar.example/login",
    }


@pytest.mark.parametrize("hops", ["0", "1"])
def test_untrusted_raw_host_cannot_generate_external_links(monkeypatch, hops):
    client = configured_app(monkeypatch, hops).test_client()
    assert (
        client.get("/proxy-test", base_url="http://attacker.invalid").status_code == 400
    )


def test_untrusted_forwarded_host_rejected_in_proxy_mode(monkeypatch):
    client = configured_app(monkeypatch, "1").test_client()
    assert (
        client.get(
            "/proxy-test", headers={"X-Forwarded-Host": "attacker.invalid"}
        ).status_code
        == 400
    )


@pytest.mark.parametrize("hops", ["0", "1"])
@pytest.mark.parametrize("peer", ["192.0.2.9", "127.0.0.1"])
def test_spoofed_loopback_forwarding_never_opens_readiness(monkeypatch, hops, peer):
    client = configured_app(monkeypatch, hops).test_client()
    response = client.get(
        "/health/ready",
        environ_overrides={"REMOTE_ADDR": peer},
        headers={"X-Forwarded-For": "127.0.0.1"},
    )
    assert response.status_code == 404


@pytest.mark.parametrize("hops", ["0", "1"])
@pytest.mark.parametrize("peer", ["127.0.0.1", "::1"])
def test_normal_loopback_healthcheck_works(monkeypatch, hops, peer):
    response = (
        configured_app(monkeypatch, hops)
        .test_client()
        .get("/health/ready", environ_overrides={"REMOTE_ADDR": peer})
    )
    assert response.status_code == 200
    assert response.json == {"status": "ready"}


def test_host_suffix_and_direct_lan_ip_configuration(monkeypatch):
    client = configured_app(
        monkeypatch, hosts=".example.com,192.168.1.50,localhost"
    ).test_client()
    for base in (
        "http://hangar.example.com",
        "http://example.com",
        "http://192.168.1.50:8087",
    ):
        assert client.get("/proxy-test", base_url=base).status_code == 204
    assert (
        client.get(
            "/proxy-test", base_url="http://example.com.attacker.invalid"
        ).status_code
        == 400
    )


def test_unset_configuration_keeps_one_proxy_and_dynamic_hosts(monkeypatch):
    monkeypatch.delenv("OPENHANGAR_PROXY_HOPS", raising=False)
    monkeypatch.delenv("OPENHANGAR_TRUSTED_HOSTS", raising=False)
    application = create_app()
    assert isinstance(application.wsgi_app, ProxyFix)
    assert application.config["PROXY_HOPS"] == 1
    assert application.config["TRUSTED_HOSTS"] is None


@pytest.mark.parametrize("value", ["-1", "one", "1.5", ""])
def test_invalid_proxy_count_fails_startup(monkeypatch, value):
    monkeypatch.setenv("OPENHANGAR_PROXY_HOPS", value)
    with pytest.raises(RuntimeError, match="OPENHANGAR_PROXY_HOPS"):
        create_app()


@pytest.mark.parametrize(
    "value",
    [
        "https://hangar.example",
        "hangar.example,,localhost",
        "*.example.com",
        "bad host",
        "user@host",
        "host/path",
        "host\\path",
        "host?query",
        "host#fragment",
    ],
)
def test_invalid_host_lists_fail_startup(monkeypatch, value):
    monkeypatch.setenv("OPENHANGAR_TRUSTED_HOSTS", value)
    with pytest.raises(RuntimeError, match="OPENHANGAR_TRUSTED_HOSTS"):
        create_app()


def test_reference_deployments_explicitly_select_proxy_or_direct_mode():
    root = Path(__file__).resolve().parents[1]
    standard = (root / "docker/docker-compose.yml").read_text()
    direct = (root / "docker/docker-compose.raspberry-pi.yml").read_text()
    assert "OPENHANGAR_PROXY_HOPS=${OPENHANGAR_PROXY_HOPS:-1}" in standard
    assert (
        "OPENHANGAR_TRUSTED_HOSTS=${OPENHANGAR_HOSTNAME},localhost,127.0.0.1"
        in standard
    )
    assert "OPENHANGAR_PROXY_HOPS=0" in direct
