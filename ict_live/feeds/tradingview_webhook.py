"""Parse a TradingView `ict_live.bar.v1` webhook payload into a 1m Bar.

Only the OFFICIAL TradingView alert() webhook is supported (see DATA_FEED_SPEC §1). Epoch-ms
timestamps are converted to tz-aware ET here so all downstream logic is ET wall-clock.
Structural OHLC validity is enforced by Bar.__post_init__ and surfaced as FeedError.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ict_live.feeds.base import Feed, FeedError, ParsedBar
from ict_live.market.bar import Bar
from ict_live.market.calendar import ET

SCHEMA = "ict_live.bar.v1"
_REQUIRED = ("schema", "symbol", "resolution", "bar_time_ms", "bar_close_ms",
             "open", "high", "low", "close", "volume")


def _ms_to_et(ms) -> datetime:
    return datetime.fromtimestamp(int(ms) / 1000.0, tz=timezone.utc).astimezone(ET)


class TradingViewWebhookFeed(Feed):
    def parse(self, payload: dict) -> ParsedBar:
        if not isinstance(payload, dict):
            raise FeedError("payload_not_object")
        for k in _REQUIRED:
            if k not in payload:
                raise FeedError(f"missing_field:{k}")
        if payload["schema"] != SCHEMA:
            raise FeedError(f"bad_schema:{payload['schema']!r}")
        if str(payload["resolution"]) != "1":
            raise FeedError(f"bad_resolution:{payload['resolution']!r}")
        try:
            o = float(payload["open"]); h = float(payload["high"])
            lo = float(payload["low"]); c = float(payload["close"]); v = float(payload["volume"])
            open_ms = int(payload["bar_time_ms"]); close_ms = int(payload["bar_close_ms"])
        except (TypeError, ValueError):
            raise FeedError("non_numeric_field")
        symbol = str(payload["symbol"])
        if not symbol:
            raise FeedError("empty_symbol")
        try:
            bar = Bar("1m", _ms_to_et(open_ms), _ms_to_et(close_ms), o, h, lo, c, v)
        except (ValueError, OverflowError, OSError) as e:
            raise FeedError(f"invalid_bar:{e}")
        return ParsedBar(symbol=symbol, bar=bar)
