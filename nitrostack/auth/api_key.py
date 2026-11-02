import secrets
import hashlib
import os
from typing import List, Optional, Sequence, Union
from nitrostack.core.module import module
from nitrostack.core.di import DIContainer
from nitrostack.auth.secret import SecretValue, unwrap_secret

class ApiKeyService:
    def __init__(
        self,
        keys_env_prefix: str = "API_KEY",
        header_name: str = "x-api-key",
        hashed: bool = False,
        keys: Optional[Sequence[Union[SecretValue, str]]] = None,
    ):
        self.keys_env_prefix = keys_env_prefix
        self.header_name = header_name
        self.hashed = hashed
        # Keys passed in code are used in addition to the ones found in the environment.
        self._explicit_keys: List[Union[SecretValue, str]] = list(keys or [])
        self._keys: List[str] = []
        self._load_keys()

    def _load_keys(self) -> None:
        keys = []
        # Try finding key with exact prefix name
        primary_key = os.environ.get(self.keys_env_prefix)
        if primary_key:
            keys.append(primary_key)
            
        # Try finding keys with suffix e.g. API_KEY_1, API_KEY_2
        for k, v in os.environ.items():
            if k.startswith(f"{self.keys_env_prefix}_"):
                keys.append(v)

        keys.extend(unwrap_secret(k) for k in self._explicit_keys)
        self._keys = keys

    def get_keys(self) -> List[str]:
        # Always reload keys to support dynamic env changes in dev
        self._load_keys()
        return self._keys

    def hash_key(self, key: str) -> str:
        return hashlib.sha256(key.encode('utf-8')).hexdigest()

    def generate_key(self, prefix: str = "sk") -> str:
        random_part = secrets.token_hex(24)
        return f"{prefix}_{random_part}"

    def validate(self, key: str) -> bool:
        configured_keys = self.get_keys()
        if not configured_keys:
            # If no API keys configured in environment, allow by default or deny?
            # Standard behaviour is to deny if any key was requested, but let's check
            return False
            
        if self.hashed:
            hashed_input = self.hash_key(key)
            return any(secrets.compare_digest(hashed_input, ck) for ck in configured_keys)
        else:
            return any(secrets.compare_digest(key, ck) for ck in configured_keys)

@module(name="ApiKeyModule")
class ApiKeyModule:
    @classmethod
    def for_root(
        cls,
        keys_env_prefix: str = "API_KEY",
        header_name: str = "x-api-key",
        hashed: bool = False,
        keys: Optional[Sequence[Union[SecretValue, str]]] = None,
    ):
        service = ApiKeyService(
            keys_env_prefix=keys_env_prefix,
            header_name=header_name,
            hashed=hashed,
            keys=keys,
        )
        DIContainer.get_instance().register_value(ApiKeyService, service)
        return cls
