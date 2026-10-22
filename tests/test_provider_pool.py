"""Verify actual TCP reuse and concurrent per-request isolation through the SDK."""

import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from order_agent.config import Settings
from order_agent.llm.transport import provider_pool, request


def response(label):
    return {
        "id": f"mock-{label}",
        "object": "chat.completion",
        "model": "test",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": json.dumps({"label": label}),
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def invoke(label, base_url, seconds=5):
    diag, usage = {}, {}
    result = request(
        [("human", label)],
        {"model": "test", "base_url": base_url, "trust_env": False},
        Settings(openai_api_key="test-" + label),
        100,
        seconds,
        diag,
        usage,
    )
    assert json.loads(result.content) == {"label": label}
    assert diag["http_status"] == 200 and diag["finish_reason"] == "stop"
    assert usage["input_tokens"] == 10 and usage["output_tokens"] == 5
    return diag


@pytest.mark.integration
def test_valid_slow_headers_use_total_deadline_without_premature_reissue():
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            # 复现供应商首包超过旧20秒局部限制、但仍在25秒总预算内的合法响应。
            time.sleep(20.25)
            body = json.dumps(response("late")).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()

        def log_message(self, *args):
            pass

    provider_pool.close()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = invoke("late", f"http://127.0.0.1:{server.server_port}/v1", 25)
        assert result["headers_ms"] >= 20_000
        assert not result.get("deadline_exceeded")
    finally:
        provider_pool.close()
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_provider_pool_reuses_real_tcp_connection_across_sdk_calls():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            label = data["messages"][-1]["content"]
            seen.append((label, self.headers["Authorization"]))
            body = json.dumps(response(label)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()

        def log_message(self, *args):
            pass

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        accepted = 0

        def get_request(self):
            self.accepted += 1
            return super().get_request()

    provider_pool.close()
    server = Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/v1"
        first = invoke("one", url)
        second = invoke("two", url)
        assert first["pool_client_reused"] is False
        assert second["pool_client_reused"] is True
        assert server.accepted == 1
        assert seen == [("one", "Bearer test-one"), ("two", "Bearer test-two")]
        assert not any(
            e["event"] == "connection.connect_tcp.started"
            for e in second.get("transport_events", [])
        )
    finally:
        provider_pool.close()
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_provider_pool_isolates_concurrent_credentials_usage_and_observers(monkeypatch):
    clients = []
    seen = {}

    async def respond(req):
        label = json.loads(req.content)["messages"][-1]["content"]
        seen[label] = req.headers["Authorization"]
        await asyncio.sleep(0.03)
        return httpx.Response(200, json=response(label), request=req)

    original = httpx.AsyncClient

    class Client(original):
        def __init__(self, **kwargs):
            clients.append(1)
            super().__init__(**kwargs, transport=httpx.MockTransport(respond))

    provider_pool.close()
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            rows = list(
                executor.map(
                    lambda label: invoke(label, "http://test/v1"), ["one", "two"]
                )
            )
        assert len(clients) == 1
        assert sorted(row["pool_client_reused"] for row in rows) == [False, True]
        assert seen == {"one": "Bearer test-one", "two": "Bearer test-two"}
    finally:
        provider_pool.close()
