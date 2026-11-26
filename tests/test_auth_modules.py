"""Phase 2 gaps — API key, JWT, and Config modules (Python-only)."""
from __future__ import annotations

import os
import time
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nitrostack import ConfigModule, ConfigService, DIContainer
from nitrostack.auth.api_key import ApiKeyModule, ApiKeyService
from nitrostack.auth.jwt import JWTModule, JWTService
from nitrostack.core.errors import ConfigurationError


def setup_function() -> None:
    DIContainer.reset()


def teardown_function() -> None:
    DIContainer.reset()


def test_api_key_loads_numbered_suffixes(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setenv("API_KEY_1", "alpha")
    monkeypatch.setenv("API_KEY_2", "beta")
    ApiKeyModule.for_root(hashed=False)
    service = DIContainer.get_instance().resolve(ApiKeyService)
    assert service.validate("alpha") is True
    assert service.validate("beta") is True
    assert service.validate("nope") is False


def test_api_key_validate_plain_and_hashed(monkeypatch):
    monkeypatch.setenv("API_KEY", "plain-secret")
    ApiKeyModule.for_root(hashed=False)
    service = DIContainer.get_instance().resolve(ApiKeyService)
    assert service.validate("plain-secret") is True
    assert service.validate("wrong") is False
    generated = service.generate_key("sk")
    assert generated.startswith("sk_")

    DIContainer.reset()
    hashed = service.hash_key("plain-secret")
    monkeypatch.setenv("API_KEY", hashed)
    ApiKeyModule.for_root(hashed=True)
    hashed_service = DIContainer.get_instance().resolve(ApiKeyService)
    assert hashed_service.validate("plain-secret") is True
    assert hashed_service.validate("nope") is False


def test_jwt_create_and_verify_roundtrip(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "unit-test-secret")
    JWTModule.for_root(secret_env_var="JWT_SECRET", audience="mcp", issuer="nitro")
    jwt = DIContainer.get_instance().resolve(JWTService)
    token = jwt.create_token({"sub": "user-1"})
    payload = jwt.verify_token(token)
    assert payload["sub"] == "user-1"
    assert payload["aud"] == "mcp"
    assert payload["iss"] == "nitro"
    try:
        jwt.verify_token("not.a.jwt")
        raise AssertionError("invalid jwt should fail")
    except ValueError:
        pass


def test_config_module_defaults_and_validation_error(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("APP_NAME=phase6\n", encoding="utf-8")
    ConfigModule.for_root(env_file_path=str(env_file), defaults={"PORT": "3000"})
    cfg = DIContainer.get_instance().resolve(ConfigService)
    assert cfg.get("APP_NAME") == "phase6"
    assert cfg.get("PORT") == "3000"
    assert cfg.get_or_throw("APP_NAME") == "phase6"

    DIContainer.reset()
    try:
        ConfigModule.for_root(ignore_env_file=True, defaults={}, validate=lambda _c: False)
        raise AssertionError("invalid config should fail")
    except ConfigurationError:
        pass



def test_jwt_rejects_expired_audience_issuer_and_bad_signature(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "unit-test-secret")
    JWTModule.for_root(secret_env_var="JWT_SECRET", audience="mcp", issuer="nitro")
    jwt = DIContainer.get_instance().resolve(JWTService)

    expired = jwt.create_token({"sub": "user-1", "exp": int(time.time()) - 10})
    try:
        jwt.verify_token(expired)
        raise AssertionError("expired jwt should fail")
    except ValueError as exc:
        assert "expired" in str(exc).lower()

    wrong_aud = jwt.create_token({"sub": "user-1", "aud": "other-api"})
    try:
        jwt.verify_token(wrong_aud)
        raise AssertionError("wrong audience should fail")
    except ValueError as exc:
        assert "audience" in str(exc).lower()

    wrong_iss = jwt.create_token({"sub": "user-1", "iss": "other-issuer"})
    try:
        jwt.verify_token(wrong_iss)
        raise AssertionError("wrong issuer should fail")
    except ValueError as exc:
        assert "issuer" in str(exc).lower()

    token = jwt.create_token({"sub": "user-1"})
    header, payload, signature = token.split(".")
    try:
        jwt.verify_token(f"{header}.{payload}.{signature[:-2]}aa")
        raise AssertionError("tampered jwt should fail")
    except ValueError as exc:
        assert "signature" in str(exc).lower() or "invalid" in str(exc).lower()


def test_jwt_parses_expires_in_units(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "unit-test-secret")
    now = int(time.time())
    for expires_in, minimum in (("30m", 29 * 60), ("45s", 40), ("3600", 3500)):
        DIContainer.reset()
        JWTModule.for_root(secret_env_var="JWT_SECRET", expires_in=expires_in)
        jwt = DIContainer.get_instance().resolve(JWTService)
        payload = jwt.verify_token(jwt.create_token({"sub": "ttl"}))
        assert payload["exp"] >= now + minimum


def test_config_env_comments_quotes_and_get_or_throw(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment line\n\nQUOTED=\"hello world\"\nSINGLE='one'\nBARE=plain\n",
        encoding="utf-8",
    )
    ConfigModule.for_root(env_file_path=str(env_file), defaults={"FALLBACK": "yes"})
    cfg = DIContainer.get_instance().resolve(ConfigService)
    assert cfg.get("QUOTED") == "hello world"
    assert cfg.get("SINGLE") == "one"
    assert cfg.get("BARE") == "plain"
    assert cfg.get("FALLBACK") == "yes"
    assert cfg.get_or_throw("BARE") == "plain"
    try:
        cfg.get_or_throw("MISSING_REQUIRED_KEY")
        raise AssertionError("missing key should throw")
    except KeyError:
        pass

    # ConfigService leaks env-file values into os.environ; undo that so the
    # ignore_env_file half of this test actually proves the file is skipped.
    for leaked in ("QUOTED", "SINGLE", "BARE"):
        monkeypatch.delenv(leaked, raising=False)
    DIContainer.reset()
    ConfigModule.for_root(env_file_path=str(env_file), ignore_env_file=True, defaults={"ONLY": "default"})
    ignored = DIContainer.get_instance().resolve(ConfigService)
    assert ignored.get("ONLY") == "default"
    assert ignored.get("BARE") is None


def test_config_unreadable_env_file_writes_stderr(tmp_path, capsys):
    blocked = tmp_path / "not-a-file"
    blocked.mkdir()
    ConfigModule.for_root(env_file_path=str(blocked), defaults={"OK": "1"})
    captured = capsys.readouterr()
    assert "ConfigModule warning" in captured.err
    assert "Failed to read" in captured.err
    assert DIContainer.get_instance().resolve(ConfigService).get("OK") == "1"


def test_api_key_empty_string_is_rejected(monkeypatch):
    monkeypatch.setenv("API_KEY", "")
    monkeypatch.delenv("API_KEY_1", raising=False)
    monkeypatch.delenv("API_KEY_2", raising=False)
    ApiKeyModule.for_root(hashed=False)
    service = DIContainer.get_instance().resolve(ApiKeyService)
    assert service.validate("") is False
    assert service.validate("anything") is False


def test_config_process_env_wins_over_env_file(tmp_path, monkeypatch):
    from nitrostack.core.app import resolve_http_port

    env_file = tmp_path / ".env"
    env_file.write_text("PORT=3000\nFROM_FILE=file\n", encoding="utf-8")
    monkeypatch.setenv("PORT", "3001")
    monkeypatch.delenv("FROM_FILE", raising=False)
    ConfigModule.for_root(env_file_path=str(env_file), defaults={"PORT": "9999", "ONLY_DEFAULT": "d"})
    cfg = DIContainer.get_instance().resolve(ConfigService)
    assert cfg.get("PORT") == "3001"
    assert resolve_http_port() == 3001
    # Keys the process doesn't set still come from the file, then defaults.
    assert cfg.get("FROM_FILE") == "file"
    assert cfg.get("ONLY_DEFAULT") == "d"
    monkeypatch.delenv("FROM_FILE", raising=False)


def test_config_env_file_port_applies_when_process_has_none(tmp_path, monkeypatch):
    from nitrostack.core.app import resolve_http_port

    env_file = tmp_path / ".env"
    env_file.write_text("PORT=3005\n", encoding="utf-8")
    monkeypatch.delenv("PORT", raising=False)
    ConfigModule.for_root(env_file_path=str(env_file), defaults={"PORT": "3000"})
    cfg = DIContainer.get_instance().resolve(ConfigService)
    assert cfg.get("PORT") == "3005"
    assert resolve_http_port() == 3005
    monkeypatch.delenv("PORT", raising=False)
