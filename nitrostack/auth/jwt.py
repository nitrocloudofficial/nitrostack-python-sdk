import time
import json
import hmac
import hashlib
import base64
import os
from typing import Any, Dict, Optional, Union
from nitrostack.core.module import module
from nitrostack.core.di import DIContainer
from nitrostack.auth.secret import SecretValue, unwrap_secret

class JWTService:
    def __init__(
        self,
        secret_env_var: str = "JWT_SECRET",
        expires_in: str = "24h",
        audience: Optional[str] = None,
        issuer: Optional[str] = None,
        secret: Union[SecretValue, str, None] = None,
    ):
        self.secret_env_var = secret_env_var
        # An explicit secret wins over the environment variable. Prefer a SecretValue.
        self._secret = secret
        self.expires_in = expires_in
        self.audience = audience
        self.issuer = issuer

    def get_secret(self) -> str:
        if self._secret is not None:
            return unwrap_secret(self._secret)
        secret = os.environ.get(self.secret_env_var)
        if not secret:
            # Fallback for dev mode/testing so it doesn't fail hard if not set
            secret = "dev_default_secret_key_nitrostack"
        return secret

    def create_token(self, payload: Dict[str, Any]) -> str:
        # Calculate expiration
        payload_copy = payload.copy()
        if "exp" not in payload_copy:
            # Parse expires_in
            seconds = 86400
            try:
                if self.expires_in.endswith("h"):
                    seconds = int(self.expires_in[:-1]) * 3600
                elif self.expires_in.endswith("m"):
                    seconds = int(self.expires_in[:-1]) * 60
                elif self.expires_in.endswith("s"):
                    seconds = int(self.expires_in[:-1])
                elif self.expires_in.isdigit():
                    seconds = int(self.expires_in)
            except Exception:
                pass
            payload_copy["exp"] = int(time.time()) + seconds
        
        if self.audience and "aud" not in payload_copy:
            payload_copy["aud"] = self.audience
        if self.issuer and "iss" not in payload_copy:
            payload_copy["iss"] = self.issuer
        if "iat" not in payload_copy:
            payload_copy["iat"] = int(time.time())

        header = {"alg": "HS256", "typ": "JWT"}

        def b64url_encode(data: bytes) -> str:
            return base64.urlsafe_b64encode(data).rstrip(b'=').decode('utf-8')

        header_b64 = b64url_encode(json.dumps(header).encode('utf-8'))
        payload_b64 = b64url_encode(json.dumps(payload_copy).encode('utf-8'))
        
        signature_base = f"{header_b64}.{payload_b64}".encode('utf-8')
        secret = self.get_secret().encode('utf-8')
        signature = hmac.new(secret, signature_base, hashlib.sha256).digest()
        signature_b64 = b64url_encode(signature)
        
        return f"{header_b64}.{payload_b64}.{signature_b64}"

    def verify_token(self, token: str) -> Dict[str, Any]:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("Invalid JWT format.")

        header_b64, payload_b64, signature_b64 = parts

        def b64url_decode(s: str) -> bytes:
            padding = '=' * (4 - len(s) % 4)
            return base64.urlsafe_b64decode(s + padding)

        # Recompute signature
        signature_base = f"{header_b64}.{payload_b64}".encode('utf-8')
        secret = self.get_secret().encode('utf-8')
        expected_sig = hmac.new(secret, signature_base, hashlib.sha256).digest()
        
        padding = '=' * (4 - len(signature_b64) % 4)
        actual_sig = base64.urlsafe_b64decode(signature_b64 + padding)

        if not hmac.compare_digest(actual_sig, expected_sig):
            raise ValueError("Invalid signature.")

        payload = json.loads(b64url_decode(payload_b64).decode('utf-8'))

        # Check expiration
        if "exp" in payload and payload["exp"] < time.time():
            raise ValueError("Token has expired.")

        # Check audience
        if self.audience and payload.get("aud") != self.audience:
            raise ValueError("Audience mismatch.")

        # Check issuer
        if self.issuer and payload.get("iss") != self.issuer:
            raise ValueError("Issuer mismatch.")

        return payload

@module(name="JWTModule")
class JWTModule:
    @classmethod
    def for_root(
        cls,
        secret_env_var: str = "JWT_SECRET",
        expires_in: str = "24h",
        audience: Optional[str] = None,
        issuer: Optional[str] = None,
        secret: Union[SecretValue, str, None] = None,
    ):
        service = JWTService(
            secret_env_var=secret_env_var,
            expires_in=expires_in,
            audience=audience,
            issuer=issuer,
            secret=secret,
        )
        DIContainer.get_instance().register_value(JWTService, service)
        return cls
