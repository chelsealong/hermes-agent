"""Real provider requests exclude local admission time from watchdog budgets."""

import json
import os
from pathlib import Path
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from run_agent import AIAgent
from agent.background_review import build_cache_parity_fork


@pytest.fixture
def backend():
    state = SimpleNamespace(entered=threading.Event(), release=threading.Event(), lock=threading.Lock(),
                            active=0, maximum=0, calls=0)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, body, content_type="application/json"):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.send(json.dumps({"default_generation_settings": {"n_ctx": 131072},
                                  "n_ctx": 131072, "data": []}).encode())

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if not self.path.endswith("/chat/completions"):
                self.send(b'{"model_info": {"llama.context_length": 131072}}')
                return
            with state.lock:
                state.calls += 1
                first = state.calls == 1
                state.active += 1
                state.maximum = max(state.maximum, state.active)
            try:
                if first:
                    state.entered.set()
                    assert state.release.wait(20)
                choice = {"index": 0, "finish_reason": "stop"}
                common = {"id": "local-test", "model": payload["model"], "created": 1}
                if payload.get("stream"):
                    result = {**common, "object": "chat.completion.chunk", "choices": [
                        {**choice, "delta": {"role": "assistant", "content": "done"}}]}
                    self.send(f"data: {json.dumps(result)}\n\ndata: [DONE]\n\n".encode(), "text/event-stream")
                else:
                    result = {**common, "object": "chat.completion", "choices": [
                        {**choice, "message": {"role": "assistant", "content": "done"}}]}
                    self.send(json.dumps(result).encode())
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                with state.lock:
                    state.active -= 1

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_port}"
    try:
        yield state
    finally:
        state.release.set()
        server.shutdown()
        thread.join(5)
        server.server_close()


def make_agent(url, platform="cli", model="local-parent"):
    return AIAgent(model=model, provider="custom", api_mode="chat_completions", api_key="test-key",
                   base_url=url, platform=platform, enabled_toolsets=[], quiet_mode=True,
                   skip_memory=True, skip_context_files=True, skip_background_review=True)


@pytest.mark.parametrize("platform", ["cli", "cron", "subagent", "background_review"])
@pytest.mark.parametrize("streaming", [False, True])
def test_real_http_serialization_excludes_queue_time(backend, platform, streaming):
    (Path(os.environ["HERMES_HOME"]) / "config.yaml").write_text('''providers:
  custom:
    stale_timeout_seconds: 20
    models:
      local-child:
        stale_timeout_seconds: 0.75
''', encoding="utf-8")
    first = make_agent(backend.url + "/v1")
    if platform == "background_review":
        second, _, _ = build_cache_parity_fork(first, max_iterations=1)
    else:
        second = make_agent(backend.url + "/router/v1", platform, "local-child")
    queued = threading.Event()
    second._emit_wait_notice = lambda text: queued.set() if text else None
    touches = []
    original_touch = second._touch_activity
    def touch(text):
        touches.append(text)
        original_touch(text)
    second._touch_activity = touch

    def call(agent, stream):
        method = agent._interruptible_streaming_api_call if stream else agent._interruptible_api_call
        return method({"model": agent.model, "messages": [{"role": "user", "content": "hello"}]})

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            holder = pool.submit(call, first, False)
            timer = None
            try:
                assert backend.entered.wait(10)
                waiter = pool.submit(call, second, streaming)
                assert queued.wait(10)
                timer = threading.Timer(2.2, backend.release.set)
                timer.start()
                assert holder.result(timeout=10).choices[0].message.content == "done"
                assert waiter.result(timeout=10).choices[0].message.content == "done"
                assert backend.maximum == 1
                assert second._consecutive_stale_streams == 0
                assert any("local backend" in text for text in touches)
            finally:
                backend.release.set()
                if timer is not None:
                    timer.cancel()
    finally:
        first.close()
        second.close()
