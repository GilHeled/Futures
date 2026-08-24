"""Telegram notifier: formats a TAKE ticket cleanly (tick-rounded prices), and is a safe no-op when
unconfigured (never hits the network or raises). No real Telegram call is made in tests."""
from ict_live.live import notify as N


def _ticket():
    return {"symbol": "COMEX:SI1!", "action": "TAKE", "structural": "SHORT",
            "entry": 69.34250259399414, "stop": 69.51499938964844, "exit_target": 68.9975,
            "risk": 0.1725, "confidence": 0.5598, "weakest_factor": "ce_distance",
            "reasoning": {"manipulation": "BSL raid #1", "mss": "confirmed"},
            "time": "2026-08-24T09:00:00-04:00"}


def test_format_ticket_rounds_prices_to_tick():
    txt = N.format_ticket(_ticket())
    assert "🔴 TAKE — SHORT COMEX:SI1! (Silver)" in txt          # short + human name
    assert "Entry 69.345" in txt and "Stop 69.515" in txt        # snapped to SI's 0.005 tick
    assert "Target +2R 69.0" in txt
    assert "risk 0.17 pts (1R)" in txt and "exec 0.56" in txt and "weakest ce_distance" in txt
    assert "manipulation BSL raid #1" in txt and "mss confirmed" in txt
    assert "69.34250259399414" not in txt                        # no raw float leaks


def test_long_uses_green_marker():
    t = _ticket(); t["structural"] = "LONG"
    assert N.format_ticket(t).startswith("🟢 TAKE — LONG")


def test_disabled_when_unconfigured_is_a_safe_noop(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    n = N.TelegramNotifier()
    assert n.enabled is False
    assert n.send("hi") is False                                 # returns False, never touches network
    assert n.notify_ticket(_ticket()) is False


def test_enabled_flag_requires_both(monkeypatch):
    assert N.TelegramNotifier(token="x", chat_id="y").enabled is True
    assert N.TelegramNotifier(token="x", chat_id=None).enabled is False
    assert N.TelegramNotifier(token=None, chat_id="y").enabled is False
