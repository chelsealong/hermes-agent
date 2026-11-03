"""Regression for #148449: local cron notes must report lost attachments."""

from cron import scheduler_delivery


def test_local_delivery_reports_media_loss(tmp_path, monkeypatch):
    media = tmp_path / "card.png"
    media.write_bytes(b"png")
    delivered = []
    monkeypatch.setattr(
        scheduler_delivery, "_resolve_delivery_targets",
        lambda *_args, **_kwargs: [{"platform": "local", "chat_id": "chat-1"}],
    )
    monkeypatch.setattr(
        "gateway.session_cron.deliver_to_local_chat",
        lambda _home, _chat_id, text, **_kwargs: delivered.append(text) or None,
    )

    error = scheduler_delivery._deliver_result(
        {"id": "job-1", "name": "card"}, f"MEDIA:{media}\n\nthe card is above"
    )

    assert len(delivered) == 1
    assert "the card is above" in delivered[0]
    assert "MEDIA:" not in delivered[0]
    assert error is not None and "1 media attachment(s) not delivered" in error
