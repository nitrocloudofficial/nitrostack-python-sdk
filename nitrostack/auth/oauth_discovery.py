"""Background HTTP discovery server (OAuth protected-resource and authorization-server
metadata, plus the simplified dynamic client registration endpoint).

Depends on ``oauth_module`` and ``cimd`` for the payloads; it never imports ``oauth``.
"""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING, Any, Dict, Optional

from nitrostack.auth.cimd import CimdFetchError, CimdValidationError
from nitrostack.auth.oauth_module import (
    apply_cimd_to_registration_body,
    build_authorization_server_metadata,
    build_protected_resource_metadata,
    build_registration_response,
    is_client_registration_enabled,
)

if TYPE_CHECKING:
    from nitrostack.auth.oauth import OAuthService


def start_discovery_server(service: "OAuthService") -> None:
    """Starts a background HTTP discovery server for OAuth Protected Resource metadata."""
    if service._server is not None:
        return

    service_instance = service
    registration_path = "/oauth/v2/register"

    def _write_json(handler: BaseHTTPRequestHandler, status: int, payload: Dict[str, Any]) -> None:
        raw = json.dumps(payload).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(raw)))
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.wfile.write(raw)

    def _write_empty(handler: BaseHTTPRequestHandler, status: int) -> None:
        handler.send_response(status)
        handler.send_header("Content-Length", "0")
        handler.send_header("Connection", "close")
        handler.end_headers()

    class DiscoveryHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            # Suppress server logging to stdout/stderr to keep stdio clean
            pass

        def do_OPTIONS(self):
            # TS discovery handlers honor CORS preflight without leaking credentials.
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()

        def _proxy_headers(self) -> Dict[str, str]:
            return {key: value for key, value in self.headers.items()}

        def _proxy_peer(self) -> Optional[str]:
            return self.client_address[0] if self.client_address else None

        def _trusted_public_origin(self) -> Optional[str]:
            from nitrostack.transports.proxy import public_origin, trusted_forwarded_host

            headers = self._proxy_headers()
            peer = self._proxy_peer()
            if not trusted_forwarded_host(headers, peer):
                return None
            return public_origin(
                headers,
                peer,
                fallback_host=f"localhost:{service_instance.discovery_port}",
                fallback_proto="http",
            )

        def do_GET(self):
            if self.path == "/.well-known/oauth-protected-resource":
                _write_json(self, 200, build_protected_resource_metadata(service_instance))
            elif self.path == "/.well-known/oauth-authorization-server":
                origin = self._trusted_public_origin()
                registration_endpoint = (
                    registration_path if is_client_registration_enabled(service_instance) else None
                )
                _write_json(
                    self,
                    200,
                    build_authorization_server_metadata(
                        service_instance,
                        registration_endpoint,
                        public_origin=origin,
                    ),
                )
            else:
                _write_empty(self, 404)

        def do_POST(self):
            if self.path != registration_path:
                _write_empty(self, 404)
                return

            if not is_client_registration_enabled(service_instance):
                _write_json(
                    self,
                    404,
                    {"error": "not_found", "error_description": "Client registration is not enabled"},
                )
                return

            length = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
            except Exception:
                body = {}

            try:
                body = apply_cimd_to_registration_body(
                    body,
                    headers=self._proxy_headers(),
                    peer=self._proxy_peer(),
                )
            except (CimdValidationError, CimdFetchError) as exc:
                _write_json(
                    self,
                    400,
                    {
                        "error": "invalid_client_metadata",
                        "error_description": str(exc),
                    },
                )
                return

            _write_json(self, 200, build_registration_response(service_instance, body))

    def run_server():
        # Try binding to OAUTH_DISCOVERY_PORT
        port = int(os.environ.get("OAUTH_DISCOVERY_PORT", service.discovery_port))
        try:
            service._server = HTTPServer(("localhost", port), DiscoveryHandler)
            # Notify client via stderr
            oauth_metadata = {
                "port": port,
                "resource_uri": service.resource_uri,
                "authorization_servers": service.authorization_servers
            }
            sys.stderr.write(
                f"[NITROSTACK_OAUTH]{json.dumps(oauth_metadata)}[/NITROSTACK_OAUTH]\n"
            )
            sys.stderr.flush()
            service._server.serve_forever()
        except Exception as e:
            sys.stderr.write(f"Failed to start OAuth Discovery Server on port {port}: {e}\n")
            sys.stderr.flush()

    service._thread = threading.Thread(target=run_server, daemon=True)
    service._thread.start()
