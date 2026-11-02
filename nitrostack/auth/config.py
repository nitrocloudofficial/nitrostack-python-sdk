import os
from typing import Dict, Any, Callable, Optional
from nitrostack.core.module import module
from nitrostack.core.di import DIContainer, injectable
from nitrostack.auth.secret import SecretValue

@injectable()
class ConfigService:
    def __init__(
        self,
        env_file_path: str = ".env",
        ignore_env_file: bool = False,
        defaults: Optional[Dict[str, Any]] = None,
        validate_fn: Optional[Callable[[Dict[str, Any]], bool]] = None,
    ):
        # ``defaults`` values may be SecretValue instances. They are kept as they are, so
        # printing the config or calling ``get()`` never shows the raw secret.
        self._config: Dict[str, Any] = {}
        if defaults:
            self._config.update(defaults)

        # Merge system environment variables first
        self._config.update(os.environ)

        # Parse .env manually (no stdout prints allowed)
        if not ignore_env_file and os.path.exists(env_file_path):
            try:
                with open(env_file_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if "=" in line:
                            k, v = line.split("=", 1)
                            k = k.strip()
                            v = v.strip().strip("'\"") # Remove wrapping quotes
                            self._config[k] = v
                            os.environ[k] = v
            except Exception as e:
                # Do NOT print to stdout! Write to stderr if necessary
                import sys
                sys.stderr.write(f"ConfigModule warning: Failed to read {env_file_path}: {e}\n")
                sys.stderr.flush()

        if validate_fn:
            if not validate_fn(self._config):
                from nitrostack.core.errors import ConfigurationError
                raise ConfigurationError("Configuration validation failed.")

    def get(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)

    def get_or_throw(self, key: str) -> Any:
        if key not in self._config:
            raise KeyError(f"Configuration key '{key}' is required but not found.")
        return self._config[key]

    def get_secret(self, key: str, default: Optional[SecretValue] = None) -> Optional[SecretValue]:
        """Return a configuration value as a ``SecretValue`` (or ``default`` when missing).

        A value that is already a ``SecretValue`` is returned as it is. A plain string is
        wrapped, so callers can pass it on without it being printed by accident.
        """
        value = self._config.get(key)
        if value is None or value == "":
            return default
        if isinstance(value, SecretValue):
            return value
        return SecretValue(str(value), source="config")

    def get_secret_or_throw(self, key: str) -> SecretValue:
        secret = self.get_secret(key)
        if secret is None:
            raise KeyError(f"Configuration key '{key}' is required but not found.")
        return secret

@module(name="ConfigModule")
class ConfigModule:
    @classmethod
    def for_root(
        cls,
        env_file_path: str = ".env",
        ignore_env_file: bool = False,
        defaults: Optional[Dict[str, Any]] = None,
        validate: Optional[Callable[[Dict[str, Any]], bool]] = None,
    ):
        service = ConfigService(
            env_file_path=env_file_path,
            ignore_env_file=ignore_env_file,
            defaults=defaults,
            validate_fn=validate
        )
        DIContainer.get_instance().register_value(ConfigService, service)
        return cls
