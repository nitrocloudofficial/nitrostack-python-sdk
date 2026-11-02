"""Issue #69: ``SecretValue`` keeps secrets out of logs, repr, pickles and serialized output."""

from __future__ import annotations

import base64
import copy
import io
import json
import logging
import pickle
import traceback
import warnings

import pytest
from pydantic import BaseModel

from nitrostack import ConfigService, DIContainer, SecretValue, is_secret_value, unwrap_secret
from nitrostack.auth.api_key import ApiKeyModule, ApiKeyService
from nitrostack.auth.jwt import JWTModule, JWTService
from nitrostack.auth.oauth import OAuthModule, OAuthService
from nitrostack.auth.oauth_module import build_registration_response
from nitrostack.auth.secret import REDACTED

RAW = "s3cr3t-value-that-must-not-leak-0123456789"


def _secret(value: str = RAW) -> SecretValue:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return SecretValue.from_value(value, allow_hardcoded=True, reason="test")


def setup_function() -> None:
    DIContainer.reset()


def teardown_function() -> None:
    DIContainer.reset()


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_from_env_reads_the_variable_and_marks_the_source(monkeypatch):
    monkeypatch.setenv("MY_SECRET", RAW)
    secret = SecretValue.from_env("MY_SECRET")
    assert secret.unwrap() == RAW
    assert secret.is_from_environment() is True


def test_from_env_raises_when_missing_and_optional_returns_none(monkeypatch):
    monkeypatch.delenv("MISSING_SECRET", raising=False)
    with pytest.raises(ValueError, match="MISSING_SECRET"):
        SecretValue.from_env("MISSING_SECRET")
    assert SecretValue.from_env_optional("MISSING_SECRET") is None
    monkeypatch.setenv("MISSING_SECRET", "")
    assert SecretValue.from_env_optional("MISSING_SECRET") is None


def test_the_missing_variable_error_never_contains_a_value(monkeypatch):
    monkeypatch.delenv("MISSING_SECRET", raising=False)
    with pytest.raises(ValueError) as exc:
        SecretValue.from_env("MISSING_SECRET")
    assert RAW not in str(exc.value)


def test_hardcoded_values_need_explicit_permission():
    with pytest.raises(ValueError, match="allow_hardcoded"):
        SecretValue.from_value(RAW)
    with pytest.warns(UserWarning, match="hardcoded"):
        secret = SecretValue.from_value(RAW, allow_hardcoded=True, reason="unit test")
    assert secret.unwrap() == RAW
    assert secret.is_from_environment() is False


def test_empty_and_non_string_values_are_rejected():
    with pytest.raises(ValueError):
        SecretValue("")
    with pytest.raises(TypeError):
        SecretValue(12345)  # type: ignore[arg-type]


def test_short_secrets_warn_but_still_work():
    with pytest.warns(UserWarning, match="shorter than 16"):
        secret = SecretValue("short")
    assert secret.unwrap() == "short"


# ---------------------------------------------------------------------------
# Acceptance criteria: the raw value never appears
# ---------------------------------------------------------------------------


def test_str_repr_and_fstring_are_redacted():
    secret = _secret()
    assert str(secret) == REDACTED
    assert repr(secret) == REDACTED
    assert f"{secret}" == REDACTED
    assert f"{secret!r}" == REDACTED
    assert "%s %r" % (secret, secret) == f"{REDACTED} {REDACTED}"
    assert "{}".format(secret) == REDACTED
    assert RAW not in f"{secret:>40}"


def test_containers_holding_a_secret_do_not_leak_in_repr():
    secret = _secret()
    assert RAW not in repr({"key": secret})
    assert RAW not in repr([secret])
    assert RAW not in repr((secret,))


def test_there_is_no_instance_dict_to_dump():
    with pytest.raises(TypeError):
        vars(_secret())


def test_logging_never_contains_the_raw_value(caplog):
    secret = _secret()
    logger = logging.getLogger("secret-test")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s %(args)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        with caplog.at_level(logging.DEBUG, logger="secret-test"):
            logger.info("token is %s", secret)
            logger.info("token is %r", secret)
            logger.info(f"token is {secret}")
            logger.info("token is %(s)s", {"s": secret})
            try:
                raise RuntimeError(f"failed with {secret}")
            except RuntimeError:
                logger.exception("boom with %s", secret)
    finally:
        logger.removeHandler(handler)
    assert RAW not in stream.getvalue()
    assert RAW not in caplog.text
    assert all(RAW not in record.getMessage() for record in caplog.records)
    assert REDACTED in caplog.text


def test_tracebacks_do_not_contain_the_raw_value():
    secret = _secret()

    def fail():
        local_secret = secret  # noqa: F841 - shows up in captured locals
        raise RuntimeError(f"bad secret {secret!r}")

    try:
        fail()
    except RuntimeError as exc:
        # capture_locals belongs to TracebackException (format_exception() has no such argument).
        captured = traceback.TracebackException(type(exc), exc, exc.__traceback__, capture_locals=True)
        text = "".join(captured.format())
    assert RAW not in text
    assert REDACTED in text


def test_pickle_never_contains_the_raw_value():
    secret = _secret()
    for protocol in range(0, pickle.HIGHEST_PROTOCOL + 1):
        blob = pickle.dumps(secret, protocol=protocol)
        assert RAW.encode() not in blob
        assert RAW.encode() not in base64.b64encode(blob)


def test_an_unpickled_secret_has_no_value_instead_of_a_fake_one():
    restored = pickle.loads(pickle.dumps(_secret()))
    assert is_secret_value(restored)
    with pytest.raises(ValueError, match="redacted during serialization"):
        restored.unwrap()
    assert bool(restored) is False
    assert restored != _secret()


class Settings(BaseModel):
    name: str
    api_secret: SecretValue


def test_pydantic_serialization_is_redacted():
    model = Settings(name="svc", api_secret=_secret())
    assert RAW not in model.model_dump_json()
    assert REDACTED in model.model_dump_json()
    assert model.model_dump()["api_secret"] == REDACTED
    assert RAW not in json.dumps(model.model_dump())
    assert RAW not in repr(model)
    assert RAW not in str(model)
    assert RAW not in json.dumps(model.model_json_schema())


def test_pydantic_accepts_a_secret_or_a_string_and_keeps_the_value():
    from_secret = Settings(name="a", api_secret=_secret())
    from_text = Settings(name="b", api_secret=RAW)
    assert from_secret.api_secret.unwrap() == RAW
    assert from_text.api_secret.unwrap() == RAW
    assert isinstance(from_text.api_secret, SecretValue)
    with pytest.raises(ValueError):
        Settings(name="c", api_secret=123)  # type: ignore[arg-type]


def test_a_bare_secret_is_not_json_serializable_rather_than_leaking():
    with pytest.raises(TypeError):
        json.dumps({"secret": _secret()})


def test_json_default_hook_that_uses_str_still_redacts():
    assert RAW not in json.dumps({"secret": _secret()}, default=str)


# ---------------------------------------------------------------------------
# Equality, hashing, copying
# ---------------------------------------------------------------------------


def test_equality_compares_values_in_constant_time(monkeypatch):
    calls: list[tuple[bytes, bytes]] = []
    real = __import__("hmac").compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr("nitrostack.auth.secret.hmac.compare_digest", spy)
    assert _secret() == _secret()
    assert _secret() == RAW
    assert _secret() != _secret("another-long-secret-value-xyz")
    assert _secret() != "different"
    assert len(calls) == 4


def test_equality_with_other_types_is_false():
    assert (_secret() == 123) is False
    assert (_secret() != 123) is True


def test_equal_secrets_hash_equal_and_the_hash_is_not_the_plain_hash():
    assert hash(_secret()) == hash(_secret())
    assert hash(_secret()) != hash(RAW)
    assert len({_secret(), _secret()}) == 1


def test_copy_and_deepcopy_keep_the_value():
    secret = _secret()
    assert copy.copy(secret).unwrap() == RAW
    nested = copy.deepcopy({"k": [secret]})
    assert nested["k"][0].unwrap() == RAW


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_is_secret_value_and_unwrap_secret():
    secret = _secret()
    assert is_secret_value(secret) is True
    assert is_secret_value(RAW) is False
    assert unwrap_secret(secret) == RAW
    assert unwrap_secret(RAW) == RAW  # plain strings are still accepted
    with pytest.raises(TypeError):
        unwrap_secret(None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        unwrap_secret(123)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Modules accept a SecretValue and still accept a plain str
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("wrap", [True, False], ids=["SecretValue", "str"])
def test_jwt_module_accepts_secret_and_str(wrap, monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    JWTModule.for_root(secret=_secret() if wrap else RAW, audience="mcp")
    service = DIContainer.get_instance().resolve(JWTService)
    assert service.get_secret() == RAW
    token = service.create_token({"sub": "u1"})
    assert service.verify_token(token)["sub"] == "u1"
    assert RAW not in token  # the secret signs the token, it is not inside it


def test_jwt_explicit_secret_wins_over_the_environment(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "from-environment-secret-value")
    service = JWTService(secret=_secret())
    assert service.get_secret() == RAW


def test_jwt_still_uses_the_environment_when_no_secret_is_given(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "from-environment-secret-value")
    assert JWTService().get_secret() == "from-environment-secret-value"


def test_jwt_service_repr_does_not_contain_the_secret():
    assert RAW not in repr(JWTService(secret=_secret()))
    assert RAW not in str(JWTService(secret=_secret()))


@pytest.mark.parametrize("wrap", [True, False], ids=["SecretValue", "str"])
def test_api_key_module_accepts_secret_and_str(wrap, monkeypatch):
    for name in ("API_KEY", "API_KEY_1"):
        monkeypatch.delenv(name, raising=False)
    ApiKeyModule.for_root(keys=[_secret() if wrap else RAW])
    service = DIContainer.get_instance().resolve(ApiKeyService)
    assert service.validate(RAW) is True
    assert service.validate("wrong") is False


def test_api_key_explicit_keys_add_to_environment_keys(monkeypatch):
    monkeypatch.setenv("API_KEY", "env-key-value")
    service = ApiKeyService(keys=[_secret()])
    assert service.validate("env-key-value") is True
    assert service.validate(RAW) is True


def test_api_key_hashed_mode_works_with_a_secret_key():
    service = ApiKeyService(hashed=True, keys=[SecretValue("x" * 16)])
    assert service.validate("anything") is False
    hashed = service.hash_key("plain")
    assert ApiKeyService(hashed=True, keys=[_secret(hashed)]).validate("plain") is True


@pytest.mark.parametrize("wrap", [True, False], ids=["SecretValue", "str"])
def test_oauth_registration_response_returns_the_real_client_secret(wrap):
    service = OAuthService(
        resource_uri="http://localhost:3000",
        authorization_servers=["http://auth.example.test"],
        scopes_supported=["read"],
        enable_client_registration=True,
        static_client_id="client-1",
        static_client_secret=_secret() if wrap else RAW,
    )
    body = build_registration_response(service)
    assert body["client_secret"] == RAW
    assert body["token_endpoint_auth_method"] == "client_secret_post"
    if wrap:
        assert RAW not in repr(service.static_client_secret)


def test_oauth_without_a_client_secret_is_a_public_client():
    service = OAuthService(
        resource_uri="http://localhost:3000",
        authorization_servers=["http://auth.example.test"],
        scopes_supported=["read"],
        enable_client_registration=True,
        static_client_id="client-1",
    )
    body = build_registration_response(service)
    assert body["client_secret"] == ""
    assert body["token_endpoint_auth_method"] == "none"


@pytest.mark.parametrize("wrap", [True, False], ids=["SecretValue", "str"])
def test_oauth_introspection_sends_the_real_secret_in_basic_auth(wrap, monkeypatch):
    monkeypatch.delenv("INTROSPECTION_CLIENT_SECRET", raising=False)
    service = OAuthService(
        resource_uri="http://localhost:3000",
        authorization_servers=["http://auth.example.test"],
        scopes_supported=["read"],
        token_introspection_endpoint="http://auth.example.test/introspect",
        token_introspection_client_id="svc",
        token_introspection_client_secret=_secret() if wrap else RAW,
    )
    seen = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"active": true}'

    def fake_urlopen(request, timeout=0):
        seen["auth"] = request.get_header("Authorization")
        return FakeResponse()

    monkeypatch.setattr("nitrostack.auth.oauth.urllib.request.urlopen", fake_urlopen)
    import asyncio

    result = asyncio.run(service._introspect_via_endpoint("tok"))
    assert result == {"active": True}
    sent = base64.b64decode(seen["auth"].split(" ", 1)[1]).decode()
    assert sent == f"svc:{RAW}"


def test_oauth_module_for_root_accepts_secret_values():
    OAuthModule.for_root(
        resource_uri="http://localhost:3000",
        authorization_servers=["http://auth.example.test"],
        scopes_supported=["read"],
        token_introspection_client_secret=_secret(),
        static_client_secret=_secret(),
    )
    service = DIContainer.get_instance().resolve(OAuthService)
    assert is_secret_value(service.static_client_secret)
    assert RAW not in repr(service.static_client_secret)


def test_config_service_keeps_secret_defaults_redacted(tmp_path):
    config = ConfigService(
        env_file_path=str(tmp_path / "missing.env"),
        defaults={"DB_PASSWORD": _secret(), "PLAIN": "x"},
    )
    assert is_secret_value(config.get("DB_PASSWORD"))
    assert RAW not in str(config.get("DB_PASSWORD"))
    assert config.get_secret("DB_PASSWORD").unwrap() == RAW


def test_config_service_get_secret_wraps_plain_strings(tmp_path):
    config = ConfigService(env_file_path=str(tmp_path / "missing.env"), defaults={"TOKEN": RAW})
    secret = config.get_secret("TOKEN")
    assert is_secret_value(secret)
    assert RAW not in f"{secret}"
    assert secret.unwrap() == RAW


def test_config_service_get_secret_missing_empty_and_required(tmp_path):
    config = ConfigService(
        env_file_path=str(tmp_path / "missing.env"),
        defaults={"EMPTY": ""},
        ignore_env_file=True,
    )
    assert config.get_secret("NOPE_NOT_SET_ANYWHERE") is None
    assert config.get_secret("EMPTY") is None
    fallback = _secret()
    assert config.get_secret("NOPE_NOT_SET_ANYWHERE", fallback) is fallback
    with pytest.raises(KeyError):
        config.get_secret_or_throw("NOPE_NOT_SET_ANYWHERE")
