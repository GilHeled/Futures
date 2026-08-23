"""One-command run entrypoint for the live MVP service.

    python -m ict_live.live.serve

Builds the ingestor with a PERSISTED raw-1m store, a LiveRunner with a persisted signal/trade log,
replays the store to recover state (warmup), and serves the FastAPI app (webhook + monitor).

Configuration is via environment variables (all optional; sane local defaults):
  ICT_LIVE_TOKEN     bearer token required on the webhook (unset = auth disabled, local only)
  ICT_LIVE_DATA_DIR  base dir for persisted state (default: ./ict_live_data)
  ICT_LIVE_HOST      bind host (default 127.0.0.1)
  ICT_LIVE_PORT      bind port (default 8000)
  ICT_LIVE_SIGNAL_TF signal timeframe (default 1H)   ICT_LIVE_ENTRY_TF entry timeframe (default 15m)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from ict_live.feeds.ingestor import Ingestor
from ict_live.live.runner import LiveRunner
from ict_live.storage.market_store import MarketStore

log = logging.getLogger("ict_live.serve")


@dataclass
class Config:
    token: str | None = None
    data_dir: str = "./ict_live_data"
    host: str = "127.0.0.1"
    port: int = 8000
    signal_tf: str = "1H"
    entry_tf: str = "15m"

    @classmethod
    def from_env(cls) -> "Config":
        e = os.environ.get
        return cls(token=e("ICT_LIVE_TOKEN") or None,
                   data_dir=e("ICT_LIVE_DATA_DIR", "./ict_live_data"),
                   host=e("ICT_LIVE_HOST", "127.0.0.1"), port=int(e("ICT_LIVE_PORT", "8000")),
                   signal_tf=e("ICT_LIVE_SIGNAL_TF", "1H"), entry_tf=e("ICT_LIVE_ENTRY_TF", "15m"))


def build_service(cfg: Config | None = None):
    """Assemble ingestor + runner with persistence, recover state, and return (app, runner, cfg)."""
    cfg = cfg or Config.from_env()
    data = Path(cfg.data_dir)
    data.mkdir(parents=True, exist_ok=True)
    store = MarketStore(path=data / "raw_1m.jsonl")           # persisted + reloaded on restart
    ing = Ingestor(token=cfg.token, store=store)
    runner = LiveRunner(ing, signal_tf=cfg.signal_tf, entry_tf=cfg.entry_tf,
                        store_dir=str(data / "signals"))
    rec = runner.warmup()                                     # replay stored 1m -> rebuild state
    log.info("warmup complete: %s", rec)
    from ict_live.api.webhook import create_app
    app = create_app(runner=runner)
    return app, runner, cfg


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = Config.from_env()
    app, _runner, _ = build_service(cfg)
    if not cfg.token:
        log.warning("ICT_LIVE_TOKEN not set — webhook auth is DISABLED (local use only).")
    import uvicorn
    log.info("serving on http://%s:%d  (monitor: /report.html)", cfg.host, cfg.port)
    uvicorn.run(app, host=cfg.host, port=cfg.port)


if __name__ == "__main__":
    main()
