"""Optional pytest fixtures. Enable with ``pytest_plugins = ["nitrostack.testing.pytest_plugin"]``.

``nitro_client`` builds a harness for the module given by a ``nitro_app`` fixture or the
``@pytest.mark.nitro_app(module=RootModule)`` marker, and closes it after the test.
"""

import pytest
import pytest_asyncio

from nitrostack.testing import NitroTestingModule


def pytest_configure(config):
    config.addinivalue_line("markers", "nitro_app(module=RootModule): root module for the nitro_client fixture")


@pytest_asyncio.fixture
async def nitro_client(request):
    marker = request.node.get_closest_marker("nitro_app")
    if marker and "module" in marker.kwargs:
        app_module = marker.kwargs["module"]
    elif "nitro_app" in request.fixturenames:
        app_module = request.getfixturevalue("nitro_app")
    else:
        raise RuntimeError(
            "nitro_client needs a 'nitro_app' fixture or @pytest.mark.nitro_app(module=RootModule)."
        )
    async with await NitroTestingModule.create(app_module) as harness:
        yield harness
