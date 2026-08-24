"""Telegram notifications for new TAKE tickets — operational only, NO trading logic.

The live service calls `TelegramNotifier.notify_ticket(ticket_dict)` exactly once when a NEW TAKE
setup opens (wired via `LiveRunner(on_take=...)`). Configured by environment:

    TELEGRAM_BOT_TOKEN   bot token from @BotFather
    TELEGRAM_CHAT_ID     your chat id (message @userinfobot, or getUpdates)

If either is missing the notifier is DISABLED and does nothing. Network calls and errors NEVER raise
into the engine path (send returns False on any failure). Uses stdlib urllib — no new dependency.
"""
from __future__ import annotations

import math
import os
import urllib.parse
import urllib.request

from ict_live import config as C

API = "https://api.telegram.org/bot{token}/sendMessage"


def _round_price(v, symbol):
    """Round a price to the instrument's tick (so alerts show 69.345, not 69.34250259399414)."""
    inst = C.INSTRUMENTS.get(symbol)
    if v is None or inst is None:
        return v
    t = inst.tick_size
    d = len(str(t).split(".")[1]) if "." in str(t) else 0
    return round(math.floor(v / t + 0.5) * t, d)     # round half UP (matches the dashboard's price())


def format_ticket(t: dict) -> str:
    """Human-readable alert text for a TAKE ticket dict (from TradeTicket.to_dict())."""
    sym = t.get("symbol", "")
    name = C.instrument_names().get(sym, "")
    struct = (t.get("structural") or "").upper()
    emoji = "🟢" if "LONG" in struct else ("🔴" if "SHORT" in struct else "⚪")
    e, s, tg = (_round_price(t.get(k), sym) for k in ("entry", "stop", "exit_target"))
    r = t.get("reasoning") or {}
    why = " · ".join(f"{k} {r[k]}" for k in ("manipulation", "mss", "fvg", "dealing_range") if r.get(k))
    risk, conf = t.get("risk"), t.get("confidence")
    head = f"{emoji} TAKE — {struct} {sym}" + (f" ({name})" if name else "")
    line2 = f"Entry {e} · Stop {s} · Target +2R {tg}"
    bits = []
    if isinstance(risk, (int, float)):
        bits.append(f"risk {risk:.2f} pts (1R)")
    if isinstance(conf, (int, float)):
        bits.append(f"exec {conf:.2f}")
    if t.get("weakest_factor"):
        bits.append(f"weakest {t['weakest_factor']}")
    lines = [head, line2]
    if bits:
        lines.append(" · ".join(bits))
    if why:
        lines.append(why)
    if t.get("time"):
        lines.append(str(t["time"]))
    return "\n".join(lines)


class TelegramNotifier:
    def __init__(self, token: str | None = None, chat_id: str | None = None):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN") or None
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID") or None

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> bool:
        """POST a message to Telegram. Returns True on success; never raises."""
        if not self.enabled:
            return False
        try:
            data = urllib.parse.urlencode({"chat_id": self.chat_id, "text": text}).encode()
            req = urllib.request.Request(API.format(token=self.token), data=data)
            with urllib.request.urlopen(req, timeout=8) as r:
                return r.status == 200
        except Exception:
            return False

    def notify_ticket(self, ticket: dict) -> bool:
        return self.send(format_ticket(ticket))


def main() -> None:
    """Quick setup check:  TELEGRAM_BOT_TOKEN=… TELEGRAM_CHAT_ID=… python -m ict_live.live.notify"""
    n = TelegramNotifier()
    if not n.enabled:
        print("Telegram DISABLED — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")
        return
    ok = n.send("✅ ict_live — Telegram alerts are wired up. You'll get a message on each new TAKE.")
    print("test message sent ✔" if ok else "send FAILED — check the token / chat id / network.")


if __name__ == "__main__":
    main()
