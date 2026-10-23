import os
from typing import Dict, Any, Callable, Optional
from nitrostack.core.module import module
from nitrostack.core.di import DIContainer, injectable

@injectable()
class ConfigService:
    def __init__(
        self,
        env_file_path: str = ".env",
        ignore_env_file: bool = False,
        defaults: Optional[Dict[str, Any]] = None,
        validate_fn: Optional[Callable[[Dict[str, Any]], bool]] = None,
    ):
        self._config: Dict[str, Any] = {}
        if defaults:
            self._config.update(defaults)

        # Merge system environment variables first
        self._config.update(os.environ)

        # Parse .env manually (no stdout prints allowed). The process
        # environment wins, as with python-dotenv and Node's dotenv: a host
        # that starts the server with PORT=3001 must not be overridden by the
        # PORT=3000 that `nitrostack init` writes into .env.
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
                            if k in os.environ:
                                continue
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
