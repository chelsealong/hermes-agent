"""#74313: usage on an invalid/discarded API response must still be billed.

The retry loop used to detect a malformed or terminal-failure response,
increment ``retry_count``, and either activate a fallback or eventually
raise — all without ever reaching the normal accounting block further
down in ``run_conversation``. Providers frequently still charge for the
tokens in that discarded response's ``usage`` block, so the old behavior
silently undercounted session cost/tokens on every retry-triggering
response.
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from agent.conversation_loop import _account_discarded_response_usage
from agent.usage_pricing import CostResult


def _agent(**overrides):
    base = dict(
        provider="openai",
        api_mode="chat_completions",
        model="some-model",
        base_url="https://example.invalid/v1",
        api_key="sk-test",
        session_id="sess-1",
        session_prompt_tokens=0,
        session_completion_tokens=0,
        session_total_tokens=0,
        session_api_calls=0,
        session_input_tokens=0,
        session_output_tokens=0,
        session_cache_read_tokens=0,
        session_cache_write_tokens=0,
        session_reasoning_tokens=0,
        session_estimated_cost_usd=0.0,
        session_cost_status="unknown",
        session_cost_source="none",
        _session_db=MagicMock(),
        _session_db_created=True,
        _ensure_db_session=MagicMock(),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _usage(prompt_tokens=100, completion_tokens=20):
    return SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)


class TestAccountDiscardedResponseUsage:
    def test_accounts_tokens_and_queues_cost(self, monkeypatch):
        agent = _agent()
        response = SimpleNamespace(usage=_usage(prompt_tokens=100, completion_tokens=20))

        monkeypatch.setattr(
            "agent.conversation_loop.estimate_usage_cost",
            lambda *a, **kw: CostResult(
                amount_usd=Decimal("0.001"), status="estimated", source="official_docs_snapshot", label="x"
            ),
        )

        _account_discarded_response_usage(agent, response)

        assert agent.session_prompt_tokens == 100
        assert agent.session_completion_tokens == 20
        assert agent.session_total_tokens == 120
        assert agent.session_api_calls == 1
        assert agent.session_estimated_cost_usd == 0.001
        assert agent.session_cost_status == "estimated"

        agent._session_db.queue_token_counts.assert_called_once()
        _, kwargs = agent._session_db.queue_token_counts.call_args
        assert kwargs["input_tokens"] == 100
        assert kwargs["output_tokens"] == 20
        assert kwargs["estimated_cost_usd"] == 0.001
        assert kwargs["model"] == "some-model"

    def test_noop_when_response_has_no_usage(self):
        agent = _agent()

        _account_discarded_response_usage(agent, SimpleNamespace(usage=None))
        _account_discarded_response_usage(agent, None)

        assert agent.session_api_calls == 0
        agent._session_db.queue_token_counts.assert_not_called()

    def test_noop_when_usage_normalizes_to_zero(self):
        agent = _agent()
        # An object with none of the recognized usage fields normalizes to
        # an all-zero CanonicalUsage — nothing billed, nothing to record.
        response = SimpleNamespace(usage=SimpleNamespace())

        _account_discarded_response_usage(agent, response)

        assert agent.session_api_calls == 0
        agent._session_db.queue_token_counts.assert_not_called()

    def test_persistence_failure_is_swallowed(self, monkeypatch):
        agent = _agent()
        agent._session_db.queue_token_counts.side_effect = RuntimeError("db closed")
        response = SimpleNamespace(usage=_usage())

        monkeypatch.setattr(
            "agent.conversation_loop.estimate_usage_cost",
            lambda *a, **kw: CostResult(amount_usd=None, status="unknown", source="none", label="n/a"),
        )

        # Must not raise even though the queued write fails — token counters
        # for this turn were already updated in-process.
        _account_discarded_response_usage(agent, response)
        assert agent.session_total_tokens == 120

    def test_skips_session_db_write_without_session_id(self, monkeypatch):
        agent = _agent(session_id=None)
        response = SimpleNamespace(usage=_usage())

        monkeypatch.setattr(
            "agent.conversation_loop.estimate_usage_cost",
            lambda *a, **kw: CostResult(amount_usd=None, status="unknown", source="none", label="n/a"),
        )

        _account_discarded_response_usage(agent, response)

        assert agent.session_total_tokens == 120
        agent._session_db.queue_token_counts.assert_not_called()

    def test_prices_moa_turn_at_real_aggregator_model(self, monkeypatch):
        """On the MoA path agent.model/provider are virtual ("closed"/"moa")
        placeholders with no pricing entry — cost estimation must use the
        real aggregator model/provider from client.last_aggregator_slot,
        same as the normal (successful-response) accounting block does."""
        agg_slot = {"model": "real-model", "provider": "real-provider", "base_url": "https://real/v1"}
        agent = _agent(
            model="closed",
            provider="moa",
            base_url=None,
            client=SimpleNamespace(last_aggregator_slot=agg_slot),
        )
        response = SimpleNamespace(usage=_usage())

        seen_kwargs = {}

        def _fake_estimate(model, usage, **kwargs):
            seen_kwargs["model"] = model
            seen_kwargs.update(kwargs)
            return CostResult(amount_usd=Decimal("0.002"), status="estimated", source="official_docs_snapshot", label="x")

        monkeypatch.setattr("agent.conversation_loop.estimate_usage_cost", _fake_estimate)

        _account_discarded_response_usage(agent, response)

        assert seen_kwargs["model"] == "real-model"
        assert seen_kwargs["provider"] == "real-provider"
        assert seen_kwargs["base_url"] == "https://real/v1"
        assert agent.session_estimated_cost_usd == 0.002
