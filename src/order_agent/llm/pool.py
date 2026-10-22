"""Process-owned async HTTP pool for callers running in synchronous graph nodes."""

import asyncio
import atexit
import concurrent.futures
import threading
from contextvars import ContextVar

import httpx

OBSERVERS = ContextVar("model_http_observers", default=None)


async def on_request(request):
    hooks = OBSERVERS.get()
    if hooks:
        await hooks[0](request)


async def on_response(response):
    hooks = OBSERVERS.get()
    if hooks:
        await hooks[1](response)


class ProviderHTTPPool:
    def __init__(self):
        self._lock = threading.Lock()
        self._loop = None
        self._thread = None
        self._clients = {}

    def _start(self):
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._loop = asyncio.new_event_loop()
                self._thread = threading.Thread(
                    target=self._serve,
                    args=(self._loop,),
                    name="model-http",
                    daemon=True,
                )
                self._thread.start()
            return self._loop

    @staticmethod
    def _serve(loop):
        asyncio.set_event_loop(loop)
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.close()

    def run(self, factory, seconds):
        # 同步图节点可能来自不同线程。所有异步客户端固定在一个I/O循环，不能跨 asyncio.run 的已关闭循环复用。
        loop = self._start()
        future = asyncio.run_coroutine_threadsafe(factory(), loop)
        try:
            return future.result(timeout=max(0.001, seconds) + 3)
        except concurrent.futures.TimeoutError:
            if not future.done():
                # 最外层也有限时；若I/O循环被本地初始化阻塞，取消排队任务而非再发一次不可见请求。
                future.cancel()
            raise

    def client(self, trust_env):
        # 此方法只在所属I/O线程调用；认证头由每次SDK请求构造，绝不放入共享客户端默认头。
        key = (httpx.AsyncClient, trust_env)
        reused = key in self._clients
        if key not in self._clients:
            self._clients[key] = httpx.AsyncClient(
                trust_env=trust_env,
                limits=httpx.Limits(
                    max_connections=16, max_keepalive_connections=8, keepalive_expiry=60
                ),
                event_hooks={"request": [on_request], "response": [on_response]},
            )
        return self._clients[key], reused

    async def _close_clients(self):
        clients, self._clients = list(self._clients.values()), {}
        await asyncio.gather(*(client.aclose() for client in clients))

    def close(self):
        with self._lock:
            loop, thread = self._loop, self._thread
            if loop is None or thread is None or not thread.is_alive():
                return
            try:
                asyncio.run_coroutine_threadsafe(self._close_clients(), loop).result(2)
            except Exception:
                pass
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=2)
            self._loop = None
            self._thread = None


provider_pool = ProviderHTTPPool()
atexit.register(provider_pool.close)
