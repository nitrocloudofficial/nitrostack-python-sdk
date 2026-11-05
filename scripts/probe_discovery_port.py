"""Does the Python OAuth discovery server survive a busy preferred port?"""
import os
import socket
import time
import urllib.request

from nitrostack.auth.oauth import OAuthModule, OAuthService
from nitrostack.core.di import DIContainer


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


port = free_port()
os.environ.pop("OAUTH_DISCOVERY_PORT", None)  # test discovery_port alone

blockers = []
for host in ("127.0.0.1", "0.0.0.0"):
    b = socket.socket()
    try:
        b.bind((host, port))
        b.listen(1)
        blockers.append(b)
    except OSError as exc:
        print(f"blocker could not bind {host}:{port}: {exc}")
        b.close()
print("busy port:", port)

DIContainer.reset()
OAuthModule.for_root(
    resource_uri="https://api.example.com",
    authorization_servers=["https://idp.example.com"],
    scopes_supported=["read"],
    discovery_port=port,
)
service = DIContainer.get_instance().resolve(OAuthService)
try:
    service.start_discovery_server()
    time.sleep(1.0)  # in case it binds in a background thread
    server = getattr(service, "_server", None)
    print("server object:", server)
    print("bound to:", getattr(server, "server_address", None))
    try:
        url = f"http://127.0.0.1:{port}/.well-known/oauth-protected-resource"
        with urllib.request.urlopen(url, timeout=2) as resp:
            print("GET", url, "->", resp.status)
    except Exception as exc:
        print("GET failed:", type(exc).__name__, exc)
except OSError as exc:
    print("FAILED on busy port:", type(exc).__name__, exc)
finally:
    try:
        service.stop_discovery_server()
    except Exception as exc:
        print("stop skipped:", type(exc).__name__, exc)
    for b in blockers:
        b.close()
    DIContainer.reset()