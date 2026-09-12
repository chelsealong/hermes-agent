"""Provider queue ownership must match the actual request worker lifetime."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from openai import OpenAI

from run_agent import AIAgent


class Backend:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.second_arrived = threading.Event()
        self.second_dispatched = False
        self.lock = threading.Lock()
        self.calls = 0

    def handle(self, request):
        with self.lock:
            self.calls += 1
            first = self.calls == 1
            if not first:
                self.second_dispatched = True
                self.second_arrived.set()
        if first:
            self.entered.set()
            assert self.release.wait(15), "test did not release the provider"
        payload = json.loads(request.content)
        choice = {"index": 0, "finish_reason": "stop"}
        common = {"id": "local-test", "model": payload["model"], "created": 1}
        if payload.get("stream"):
            chunk = {**common, "object": "chat.completion.chunk", "choices": [
                {**choice, "delta": {"role": "assistant", "content": "done"}}]}
            return httpx.Response(200, content=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
                                  headers={"content-type": "text/event-stream"})
        response = {**common, "object": "chat.completion", "choices": [
            {**choice, "message": {"role": "assistant", "content": "done"}}]}
        return httpx.Response(200, json=response)


def make_agent(backend, platform="cli"):
    agent = AIAgent(
        model="gpt-4.1", provider="custom", api_mode="chat_completions",
        api_key="test-key", base_url="http://127.0.0.1:11434/v1",
        quiet_mode=True, skip_context_files=True, skip_memory=True,
        skip_background_review=True, enabled_toolsets=[], platform=platform,
    )
    agent._create_request_openai_client = lambda **kwargs: OpenAI(
        api_key="test-key", base_url=agent.base_url,
        http_client=httpx.Client(transport=httpx.MockTransport(backend.handle)),
        max_retries=0,
    )
    return agent


def call(agent, streaming):
    kwargs = {"model": agent.model, "messages": [{"role": "user", "content": "hello"}]}
    method = agent._interruptible_streaming_api_call if streaming else agent._interruptible_api_call
    return method(kwargs)


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("platform", ["cli", "cron", "subagent"])
@pytest.mark.parametrize("interruption", ["active", "queued", "slot_released"])
def test_interrupted_request_cannot_dispatch_or_release_a_live_worker(streaming, platform, interruption):
    backend = Backend()
    first = make_agent(backend)
    second = make_agent(backend, platform)
    notices = []

    with ThreadPoolExecutor(max_workers=2) as pool:
        current = pool.submit(call, first, streaming)

        def notice(text):
            notices.append(text)
            if text:
                if interruption == "slot_released":
                    second.interrupt("cancel queued request")
                    backend.release.set()
                    current.result(timeout=10)
                backend.second_arrived.set()

        second._emit_wait_notice = notice
        try:
            assert backend.entered.wait(10)
            if interruption == "active":
                first.interrupt("cancel active request")
                with pytest.raises(InterruptedError):
                    current.result(timeout=10)
                first._interrupt_requested = False  # The next turn clears the shared flag.
            queued = pool.submit(call, second, streaming)
            assert backend.second_arrived.wait(10)
            if interruption == "active":
                assert not backend.second_dispatched, "interrupted worker still owns the provider"
            else:
                if interruption == "queued":
                    second.interrupt("cancel queued request")
                with pytest.raises(InterruptedError):
                    queued.result(timeout=10)
                second._interrupt_requested = False
                assert backend.calls == 1, "queued cancellation sent a new request"
                assert notices[-1] == "", "queued cancellation left a stale wait notice"
        finally:
            backend.release.set()
        if interruption == "active":
            assert queued.result(timeout=10).choices[0].message.content == "done"
            assert notices[-1] == ""
        else:
            assert current.result(timeout=10).choices[0].message.content == "done"
            assert backend.calls == 1, "retired waiter dispatched after the next turn reset"
