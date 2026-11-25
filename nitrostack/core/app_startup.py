"""Startup implementation mixin."""

import logging
import os
from typing import Any, Optional

from nitrostack.core.app_config import resolve_http_host, resolve_http_port
from nitrostack.core.di import DIContainer
from nitrostack.protocol.version import (
    ProtocolEra,
    resolve_http_engine,
    resolve_protocol_era_resolution,
    wire_mode_for_era,
)

logger = logging.getLogger(__name__)


class StartupMixin:
    def _apply_protocol_era(self) -> ProtocolEra:
        """Re-read env/config, store the era, and log how it was chosen."""
        resolution = resolve_protocol_era_resolution(config_value=self.server_config.protocol_era)
        self.protocol_era = resolution.era
        self.protocol_era_source = resolution.source
        logger.info(resolution.log_line())
        return resolution.era

    def get_combined_app(
        self,
        *,
        max_sessions: Optional[int] = None,
        session_idle_timeout: Optional[float] = None,
        enable_cors: Optional[bool] = None,
        stateless: Optional[bool] = None,
        json_response: Optional[bool] = None,
    ) -> Any:
        """
        Build the Starlette app wiring the owned low-level server to the Streamable
        HTTP (`/mcp`), legacy SSE (`/sse` + `/mcp/messages/`), and health-check
        (`/mcp/health`) endpoints. See `nitrostack.transports.http.build_http_app`
        for the full behavior (session cap, CORS, DNS-rebinding protection).

        Any argument left as ``None`` falls back to env
        (``MCP_STATELESS``, ``NITRO_MCP_PROTOCOL_VERSION``, ``ENABLE_CORS``)
        then this app's ``ServerConfig.protocol_era``. Unset protocol era is ``auto``.
        """
        from nitrostack.transports.http import build_http_app

        era = self._apply_protocol_era()
        wire_mode = wire_mode_for_era(era)
        # Sessionful 1.x only when era is legacy. auto/modern stay sessionless.
        http_engine = resolve_http_engine(era, stateless=stateless)
        effective_stateless = http_engine == "sessionless"

        if enable_cors is None:
            env_cors = self._env_bool("ENABLE_CORS")
            enable_cors = True if env_cors is None else env_cors

        http_app = build_http_app(
            self,
            max_sessions=max_sessions
            if max_sessions is not None
            else self.server_config.max_sessions,
            session_idle_timeout=session_idle_timeout,
            enable_cors=enable_cors,
            stateless=effective_stateless,
            json_response=self.server_config.json_response
            if json_response is None
            else json_response,
            protocol_era=era,
            wire_mode=wire_mode,
            http_engine=http_engine,
        )

        # The official MCP SDK owns the complete Streamable HTTP request lifecycle
        # mounted by ``build_http_app``.  Do not wrap it in a second JSON-RPC
        # dispatcher: a sidecar would answer ping/initialize/tools/call itself and
        # could diverge from the protocol and session behaviour of the SDK.
        return http_app

    async def _run_stdio(self) -> None:
        from nitrostack.transports.stdio import run_stdio

        await run_stdio(self.mcp_server, self.protocol_era)

    @staticmethod
    def _env_int(name: str) -> Optional[int]:
        raw = os.environ.get(name)
        if raw is None or raw == "":
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    @staticmethod
    def _env_bool(name: str) -> Optional[bool]:
        raw = os.environ.get(name)
        if raw is None or raw == "":
            return None
        return raw.strip().lower() in ("1", "true", "yes", "on")

    async def start(self) -> None:
        """Starts the MCP application based on transport configurations."""
        # Start background OAuth discovery only when OAuthModule registered a service.
        # resolve(OAuthService) would otherwise auto-instantiate and fail closed.
        from nitrostack.auth.oauth import OAuthService, warn_if_oauth_fail_open

        container = DIContainer.get_instance()
        if container.has_value(OAuthService):
            oauth_service = container.resolve(OAuthService)
            oauth_service.start_discovery_server()
            warn_if_oauth_fail_open()

        transport = os.environ.get("MCP_TRANSPORT_TYPE") or self.server_config.transport_type
        node_env = os.environ.get("NODE_ENV", "development")
        port = resolve_http_port()
        host = resolve_http_host()

        json_response = self._env_bool("MCP_JSON_RESPONSE")
        if json_response is None:
            json_response = self.server_config.json_response
        max_sessions = self._env_int("MCP_MAX_SESSIONS") or self.server_config.max_sessions
        session_timeout_ms = (
            self._env_int("MCP_SESSION_TIMEOUT_MS") or self.server_config.session_timeout_ms
        )
        session_idle_timeout = (session_timeout_ms / 1000) if session_timeout_ms else None
        graceful_timeout_ms = self._env_int("MCP_GRACEFUL_SHUTDOWN_TIMEOUT_MS") or 10000

        if transport == "http":
            import uvicorn

            app = self.get_combined_app(
                max_sessions=max_sessions,
                session_idle_timeout=session_idle_timeout,
                json_response=json_response,
            )
            config = uvicorn.Config(
                app,
                host=host,
                port=port,
                log_level="info",
                timeout_graceful_shutdown=graceful_timeout_ms / 1000,
            )
            server = uvicorn.Server(config)
            await server.serve()
        elif transport == "dual" or (node_env == "production" and not transport):
            from nitrostack.transports.dual import run_dual

            app = self.get_combined_app(
                max_sessions=max_sessions,
                session_idle_timeout=session_idle_timeout,
                json_response=json_response,
            )
            await run_dual(
                self,
                app,
                host=host,
                port=port,
                graceful_timeout=graceful_timeout_ms / 1000,
            )
        else:
            # Default Stdio
            self._apply_protocol_era()
            from nitrostack.transports.stdio import safe_stdio_transport

            with safe_stdio_transport():
                await self._run_stdio()
