"""Transport- and token-level hardening: response headers, request-size
refusal, pinned token claims, and signing-key rotation."""

from __future__ import annotations

from collections.abc import Iterator

import jwt as pyjwt
import pytest
from httpx import AsyncClient

from api.core import security
from api.core.config import get_settings
from api.main import create_app


@pytest.fixture
def settings_sandbox(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # get_settings is cached, so both the change and the restore must bust it
    # or later tests inherit this one's settings.
    get_settings.cache_clear()
    yield
    monkeypatch.undo()
    get_settings.cache_clear()


async def test_api_responses_carry_security_headers(client: AsyncClient) -> None:
    headers = (await client.get("/health")).headers

    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["cross-origin-opener-policy"] == "same-origin"
    assert headers["cross-origin-resource-policy"] == "same-origin"
    assert "camera=()" in headers["permissions-policy"]
    # Inert over plaintext; takes effect the moment this is served over TLS.
    assert "max-age=" in headers["strict-transport-security"]


async def test_api_responses_carry_a_locked_down_csp(client: AsyncClient) -> None:
    csp = (await client.get("/health")).headers["content-security-policy"]

    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "base-uri 'none'" in csp


async def test_interactive_docs_are_exempt_from_the_csp(client: AsyncClient) -> None:
    """Swagger's CDN assets would be blocked by default-src 'none'; the
    exemption is why /docs still renders."""
    response = await client.get("/docs")

    assert response.status_code == 200
    assert "content-security-policy" not in response.headers


async def test_error_responses_are_covered_too(client: AsyncClient) -> None:
    """Headers come from middleware, so a 401 gets them as surely as a 200."""
    response = await client.get("/auth/me")

    assert response.status_code == 401
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_oversized_request_body_is_refused(client: AsyncClient) -> None:
    limit = get_settings().max_request_body_bytes

    response = await client.post(
        "/auth/register",
        content=b"x" * (limit + 1),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413


async def test_malformed_content_length_is_refused(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/register",
        content=b"{}",
        headers={"content-type": "application/json", "content-length": "not-a-number"},
    )

    assert response.status_code == 400


async def test_tokens_carry_kid_issuer_and_audience(owner) -> None:
    settings = get_settings()

    header = pyjwt.get_unverified_header(owner.access_token)
    claims = pyjwt.decode(owner.access_token, options={"verify_signature": False})

    assert "kid" in header
    assert header["kid"] != settings.jwt_secret_key
    assert settings.jwt_secret_key not in header["kid"]
    assert claims["iss"] == settings.jwt_issuer
    assert claims["aud"] == settings.jwt_audience


@pytest.mark.parametrize(
    ("label", "overrides"),
    [
        ("issuer", {"iss": "some-other-deployment"}),
        ("audience", {"aud": "some-other-api"}),
    ],
)
async def test_token_from_another_deployment_is_rejected(
    label: str, overrides: dict[str, str]
) -> None:
    """Even signed with our own secret, a token minted elsewhere is refused --
    what stops a staging token being replayed against production."""
    settings = get_settings()
    claims = {
        "sub": "00000000-0000-0000-0000-000000000001",
        "type": security.ACCESS_TOKEN,
        "jti": "abc",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "exp": 9_999_999_999,
        **overrides,
    }
    forged = pyjwt.encode(claims, settings.jwt_secret_key, algorithm="HS256")

    with pytest.raises(Exception):
        security.decode_token(forged, security.ACCESS_TOKEN)


async def test_forged_token_signed_with_the_wrong_key_is_rejected() -> None:
    settings = get_settings()
    claims = {
        "sub": "00000000-0000-0000-0000-000000000001",
        "type": security.ACCESS_TOKEN,
        "jti": "abc",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "exp": 9_999_999_999,
    }
    forged = pyjwt.encode(claims, "an-attacker-supplied-signing-key", algorithm="HS256")

    with pytest.raises(Exception):
        security.decode_token(forged, security.ACCESS_TOKEN)


async def test_key_rotation_keeps_existing_tokens_valid(
    monkeypatch: pytest.MonkeyPatch, settings_sandbox: None
) -> None:
    """Rotating the signing key must not log every user out at once."""
    original = get_settings().jwt_secret_key
    token = security.create_access_token("22222222-2222-2222-2222-222222222222")
    original_kid = pyjwt.get_unverified_header(token)["kid"]

    monkeypatch.setenv("JWT_SECRET_KEY", "a-freshly-rotated-signing-key-0123456789ab")
    monkeypatch.setenv("PREVIOUS_JWT_SECRET_KEYS", original)
    get_settings.cache_clear()

    # Old token still verifies, because the retired key is still on the ring.
    payload = security.decode_token(token, security.ACCESS_TOKEN)
    assert payload["sub"] == "22222222-2222-2222-2222-222222222222"

    fresh = security.create_access_token("33333333-3333-3333-3333-333333333333")
    assert pyjwt.get_unverified_header(fresh)["kid"] != original_kid


async def test_dropping_a_retired_key_invalidates_its_tokens(
    monkeypatch: pytest.MonkeyPatch, settings_sandbox: None
) -> None:
    """Removing a key from the ring is what actually revokes its tokens."""
    token = security.create_access_token("44444444-4444-4444-4444-444444444444")

    monkeypatch.setenv("JWT_SECRET_KEY", "a-freshly-rotated-signing-key-0123456789ab")
    monkeypatch.setenv("PREVIOUS_JWT_SECRET_KEYS", "")
    get_settings.cache_clear()

    with pytest.raises(Exception):
        security.decode_token(token, security.ACCESS_TOKEN)


async def test_wildcard_cors_origin_is_refused_at_startup(
    monkeypatch: pytest.MonkeyPatch, settings_sandbox: None
) -> None:
    """Starlette implements wildcard-plus-credentials by echoing the caller's
    origin, so it must fail loudly at boot rather than be served quietly."""
    monkeypatch.setenv("CORS_ORIGINS", "*")
    get_settings.cache_clear()

    with pytest.raises(RuntimeError, match="may not contain"):
        create_app()
