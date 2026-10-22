"""Bounded provider I/O with timings; no prompt, credentials or reasoning text in telemetry."""

import asyncio
import time

import httpx
from langchain_openai import ChatOpenAI

from order_agent.llm.pool import OBSERVERS, provider_pool


async def _request(
    messages, configuration, cfg, max_output, seconds, diagnostics, usage
):
    started = time.monotonic()

    def elapsed():
        return round((time.monotonic() - started) * 1000)

    async def sent(request):
        diagnostics["request_sent_ms"] = elapsed()

        async def trace(name, info):
            if name.startswith(
                (
                    "connection.",
                    "proxy.",
                    "http11.receive_response_headers",
                    "http2.receive_response_headers",
                )
            ):
                events = diagnostics.setdefault("transport_events", [])
                if len(events) < 24:
                    events.append({"event": name, "ms": elapsed()})

        request.extensions["trace"] = trace

    async def headers(response):
        diagnostics.update(headers_ms=elapsed(), http_status=response.status_code)
        original = response.stream

        class ObservedStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                async for packet in original:
                    diagnostics.setdefault("first_body_bytes_ms", elapsed())
                    diagnostics["body_bytes"] = diagnostics.get("body_bytes", 0) + len(
                        packet
                    )
                    yield packet

            async def aclose(self):
                await original.aclose()

        response.stream = ObservedStream()

    async def credentials():
        return cfg.openai_api_key.get_secret_value()

    # 已观测到合法响应等到约40秒才来首包；20秒局部读超时会提前重发，浪费同一45秒总预算。
    # 连接/TLS/读取共用剩余时间，asyncio的绝对deadline兜底；池排队仍限5秒，不能无限等待连接槽。
    timeout = httpx.Timeout(seconds, pool=min(5, seconds))
    try:
        # 超时只由这一次剩余总预算决定；流式小包不会为任务续命，重试也不会重新获得45秒。
        async with asyncio.timeout(seconds):
            token = OBSERVERS.set((sent, headers))
            try:
                client, reused = provider_pool.client(
                    configuration.get("trust_env", True)
                )
                diagnostics["pool_client_reused"] = reused
                model = ChatOpenAI(
                    model=configuration["model"],
                    base_url=configuration["base_url"],
                    api_key=credentials,
                    temperature=0,
                    timeout=timeout,
                    max_retries=0,
                    max_tokens=max_output,
                    http_async_client=client,
                    http_socket_options=(),
                    extra_body={"enable_thinking": configuration["enable_thinking"]}
                    if "enable_thinking" in configuration
                    else {},
                ).bind(response_format={"type": "json_object"})
                if configuration.get("streaming", False):
                    message = None
                    async for chunk in model.astream(messages, stream_usage=True):
                        diagnostics.setdefault("first_chunk_ms", elapsed())
                        if chunk.content:
                            diagnostics.setdefault("first_content_ms", elapsed())
                        reasoning = (
                            chunk.additional_kwargs.get("reasoning_content") or ""
                        )
                        diagnostics["reasoning_chars"] = diagnostics.get(
                            "reasoning_chars", 0
                        ) + len(reasoning)
                        message = chunk if message is None else message + chunk
                        if chunk.usage_metadata:
                            usage.update(chunk.usage_metadata)
                    if message is None:
                        raise ValueError("Empty model stream")
                else:
                    message = await model.ainvoke(messages)
                    diagnostics["first_content_ms"] = elapsed()
                usage.update(message.usage_metadata or {})
                diagnostics.update(
                    finish_reason=message.response_metadata.get("finish_reason"),
                    response_model=message.response_metadata.get("model_name"),
                    content_chars=len(message.content)
                    if isinstance(message.content, str)
                    else None,
                    cached_input_tokens=usage.get("input_token_details", {}).get(
                        "cache_read", 0
                    ),
                    reasoning_tokens=usage.get("output_token_details", {}).get(
                        "reasoning", 0
                    ),
                )
                return message
            finally:
                OBSERVERS.reset(token)
    except Exception as error:
        chain = []
        cause = error
        while cause is not None and len(chain) < 4:
            chain.append(type(cause).__name__)
            cause = cause.__cause__
        diagnostics.update(
            error_chain=chain,
            failure_stage="waiting_headers"
            if "headers_ms" not in diagnostics
            else "waiting_content"
            if "first_body_bytes_ms" not in diagnostics
            else "receiving_body",
            http_status=getattr(error, "status_code", diagnostics.get("http_status")),
        )
        if isinstance(error, TimeoutError):
            diagnostics["deadline_exceeded"] = True
        raise
    finally:
        diagnostics["elapsed_ms"] = elapsed()


def request(messages, configuration, cfg, max_output, seconds, diagnostics, usage):
    started = time.monotonic()

    async def perform():
        return await _request(
            messages,
            configuration,
            cfg,
            max_output,
            max(0.001, seconds - (time.monotonic() - started)),
            diagnostics,
            usage,
        )

    try:
        return provider_pool.run(perform, seconds)
    except TimeoutError:
        diagnostics.setdefault("deadline_exceeded", True)
        diagnostics.setdefault("failure_stage", "io_loop_deadline")
        diagnostics.setdefault("elapsed_ms", round((time.monotonic() - started) * 1000))
        raise
