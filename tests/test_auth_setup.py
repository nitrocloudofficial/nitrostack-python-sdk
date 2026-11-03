"""Issue #70: quick-setup helpers and environment validation for JWT, API key and OAuth."""

from __future__ import annotations

import pytest

from nitrostack import (
    ApiKeyModule,
    DIContainer,
    JWTModule,
    OAuthModule,
    SecretValue,
    generate_test_credentials,
    print_auth_setup_instructions,
    setup_api_key_auth,
    setup_jwt_auth,
    setup_oauth_auth,
    validate_auth_env,
)
from nitrostack.auth.api_key import ApiKeyService
from nitrostack.auth.jwt import JWTService
from nitrostack.auth.oauth import OAuthService
from nitrostack.core.errors import ConfigurationError

_AUTH_ENV = (
    "JWT_SECRET", "TOKEN_AUDIENCE", "TOKEN_ISSUER", "RESOURCE_URI", "AUTH_SERVER_URL",
    "JWKS_URI", "OAUTH_INTROSPECTION_ENDPOINT", "INTROSPECTION_ENDPOINT",
)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    import os

    for name in list(os.environ):
        if name in _AUTH_ENV or name == "API_KEY" or name.startswith("API_KEY_"):
            monkeypatch.delenv(name)
    DIContainer.reset()
    yield
    DIContainer.reset()


def test_jwt_config_configures_module(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    cfg = setup_jwt_auth()
    JWTModule.for_root(**cfg)
    service = DIContainer.get_instance().resolve(JWTService)
    token = service.create_token({"sub": "u1"})
    assert service.verify_token(token)["sub"] == "u1"
    assert service.get_secret() == "x" * 40


def test_jwt_explicit_secret_and_plain_str(monkeypatch):
    cfg = setup_jwt_auth(secret="y" * 40, audience="aud")
    assert isinstance(cfg["secret"], SecretValue)
    JWTModule.for_root(**cfg)
    service = DIContainer.get_instance().resolve(JWTService)
    assert service.audience == "aud"
    assert service.get_secret() == "y" * 40


def test_jwt_missing_secret_raises():
    with pytest.raises(ConfigurationError, match="JWT_SECRET"):
        setup_jwt_auth()


def test_api_key_config_from_explicit_keys():
    cfg = setup_api_key_auth(keys=["k" * 32, SecretValue("m" * 32)])
    ApiKeyModule.for_root(**cfg)
    service = DIContainer.get_instance().resolve(ApiKeyService)
    assert set(service.get_keys()) == {"k" * 32, "m" * 32}


def test_api_key_config_from_env(monkeypatch):
    monkeypatch.setenv("API_KEY_1", "a" * 32)
    cfg = setup_api_key_auth(header_name="x-token")
    assert cfg["keys"] == []
    ApiKeyModule.for_root(**cfg)
    service = DIContainer.get_instance().resolve(ApiKeyService)
    assert service.get_keys() == ["a" * 32]
    assert service.header_name == "x-token"


def test_api_key_missing_raises():
    with pytest.raises(ConfigurationError, match="API_KEY"):
        setup_api_key_auth()


def test_oauth_config_from_env_configures_module(monkeypatch):
    monkeypatch.setenv("RESOURCE_URI", "https://srv/mcp")
    monkeypatch.setenv("AUTH_SERVER_URL", "https://auth.example")
    monkeypatch.setenv("JWKS_URI", "https://auth.example/jwks")
    cfg = setup_oauth_auth(scopes_supported=["read"])
    OAuthModule.for_root(**cfg)
    service = DIContainer.get_instance().resolve(OAuthService)
    assert service.resource_uri == "https://srv/mcp"
    assert service.authorization_servers == ["https://auth.example"]
    assert service.scopes_supported == ["read"]
    assert service.jwks_uri == "https://auth.example/jwks"


def test_oauth_overrides_and_secret_wrapping():
    cfg = setup_oauth_auth(
        resource_uri="https://srv/mcp",
        authorization_servers=["https://auth.example"],
        token_introspection_endpoint="https://auth.example/introspect",
        token_introspection_client_secret="z" * 32,
    )
    assert isinstance(cfg["token_introspection_client_secret"], SecretValue)
    OAuthModule.for_root(**cfg)
    service = DIContainer.get_instance().resolve(OAuthService)
    assert service.token_introspection_endpoint == "https://auth.example/introspect"


def test_oauth_reports_every_missing_variable():
    with pytest.raises(ConfigurationError) as err:
        setup_oauth_auth()
    text = str(err.value)
    assert "RESOURCE_URI" in text and "AUTH_SERVER_URL" in text and "JWKS_URI" in text


def test_validate_lists_all_missing_not_just_first():
    valid, missing = validate_auth_env("oauth")
    assert valid is False
    assert len(missing) == 3
    assert missing[0] == "RESOURCE_URI" and missing[1] == "AUTH_SERVER_URL"
    assert "JWKS_URI" in missing[2] and "OAUTH_INTROSPECTION_ENDPOINT" in missing[2]


def test_validate_partial_and_complete(monkeypatch):
    monkeypatch.setenv("RESOURCE_URI", "https://srv/mcp")
    assert validate_auth_env("oauth")[1] == [m for m in validate_auth_env("oauth")[1] if "RESOURCE" not in m]
    assert len(validate_auth_env("oauth")[1]) == 2
    monkeypatch.setenv("AUTH_SERVER_URL", "https://a")
    monkeypatch.setenv("INTROSPECTION_ENDPOINT", "https://a/i")
    assert validate_auth_env("oauth") == (True, [])


def test_validate_jwt_and_api_key(monkeypatch):
    assert validate_auth_env("jwt") == (False, ["JWT_SECRET"])
    monkeypatch.setenv("JWT_SECRET", "  ")
    assert validate_auth_env("jwt")[0] is False
    monkeypatch.setenv("JWT_SECRET", "s" * 32)
    assert validate_auth_env("jwt") == (True, [])
    assert validate_auth_env("api_key")[0] is False
    monkeypatch.setenv("API_KEY_2", "k" * 32)
    assert validate_auth_env("api_key") == (True, [])


def test_validate_unknown_kind():
    with pytest.raises(ValueError):
        validate_auth_env("saml")


def test_test_credentials_random_each_call_and_not_printed(capsys, caplog):
    first = generate_test_credentials()
    second = generate_test_credentials()
    assert first["jwt_secret"].unwrap() != second["jwt_secret"].unwrap()
    assert first["api_key"].unwrap() != second["api_key"].unwrap()
    raw = first["jwt_secret"].unwrap()
    assert raw not in repr(first) and raw not in str(first)
    assert capsys.readouterr().out == ""
    assert raw not in caplog.text


def test_test_credentials_configure_modules():
    creds = generate_test_credentials()
    JWTModule.for_root(**setup_jwt_auth(secret=creds["jwt_secret"]))
    ApiKeyModule.for_root(**setup_api_key_auth(keys=[creds["api_key"]]))
    di = DIContainer.get_instance()
    assert di.resolve(JWTService).get_secret() == creds["jwt_secret"].unwrap()
    assert di.resolve(ApiKeyService).get_keys() == [creds["api_key"].unwrap()]


@pytest.mark.parametrize("kind", ["jwt", "api_key", "oauth"])
def test_print_instructions(kind, capsys):
    print_auth_setup_instructions(kind)
    out = capsys.readouterr().out
    assert "for_root" in out


def test_config_repr_hides_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "topsecret-" + "q" * 30)
    assert "topsecret" not in repr(setup_jwt_auth())
