import asyncio
import json
import os

import anyio
import pytest
from fastapi.testclient import TestClient

# Tests exercise MQTT callbacks with fakes; never subscribe the test app to live hardware.
os.environ["SKYGUARD_MQTT_PORT"] = "65534"

from app.main import app
from app.services import engine


_runtime_client = None


async def _await_coroutine(coroutine):
    return await coroutine


def run_async(coroutine):
    """Run engine work on the one ASGI event loop owned by the test session."""
    if _runtime_client is None or _runtime_client.portal is None:
        raise RuntimeError("SkyGuard test runtime is not active")
    return _runtime_client.portal.call(_await_coroutine, coroutine)


def receive_websocket_events(websocket, required_types, timeout_seconds=2.0):
    """Receive until all required event types arrive, with a hard failure bound."""
    required = set(required_types)
    received = []

    async def receive_required():
        with anyio.fail_after(timeout_seconds):
            while not required <= {message["type"] for message in received}:
                frame = await websocket._send_rx.receive()
                websocket._raise_on_close(frame)
                received.append(json.loads(frame["text"]))
        return received

    try:
        return websocket.portal.call(receive_required)
    except TimeoutError as exc:
        seen = [message["type"] for message in received]
        raise AssertionError(
            f"Timed out waiting for WebSocket events {sorted(required)}; received {seen}"
        ) from exc


async def _assert_no_runtime_leak():
    # Runtime simulator has been removed. Only assert that tests do not leak
    # WebSocket clients between cases.
    assert not engine.clients


async def _reset_test_runtime():
    assert not engine.clients, "A previous WebSocket test leaked an engine client"
    engine._reset_all_state_for_testing()


@pytest.fixture(scope="session")
def app_runtime():
    global _runtime_client
    with TestClient(app) as test_client:
        _runtime_client = test_client
        try:
            yield test_client
        finally:
            _runtime_client = None
    assert engine.monitor_task is None


@pytest.fixture(autouse=True)
def fresh_engine(app_runtime):
    run_async(_reset_test_runtime())
    run_async(_assert_no_runtime_leak())
    yield
    run_async(_reset_test_runtime())
    run_async(_assert_no_runtime_leak())


@pytest.fixture
def client(app_runtime):
    return app_runtime
