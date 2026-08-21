"""Thin subprocess wrapper over the TradingView MCP `tv` CLI (JSON output).

DEV-ONLY. This is the ONLY module that knows the MCP exists. It shells out to the `tv` CLI
(the repo exposes every MCP tool as a `tv` command with JSON output) rather than embedding an
MCP client, so nothing in the engine ever links against it. The engine MUST NOT import this.

It never raises for a failed command: every call returns a TvResult so a probe/audit can record
"unreachable" or "error" as data. Binary path via arg or $TV_CLI (default "tv"); optional cwd
(e.g. the cloned repo) so `node src/cli/index.js` style installs work via TV_CLI="node …".
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class TvResult:
    ok: bool
    cmd: list[str]
    data: Any = None          # parsed JSON stdout, or None if not JSON
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    error: Optional[str] = None   # transport-level error (not-found/timeout), not tool error


class TvClient:
    def __init__(self, binary: Optional[str] = None, cwd: Optional[str] = None,
                 timeout: float = 30.0):
        raw = binary or os.environ.get("TV_CLI", "tv")
        # allow TV_CLI="node /path/src/cli/index.js"
        self._argv0 = raw.split()
        self.cwd = cwd
        self.timeout = timeout

    def available(self) -> bool:
        exe = self._argv0[0]
        return shutil.which(exe) is not None or os.path.exists(exe)

    def run(self, *args, timeout: Optional[float] = None) -> TvResult:
        cmd = [*self._argv0, *[str(a) for a in args]]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=timeout or self.timeout, cwd=self.cwd)
        except FileNotFoundError:
            return TvResult(False, cmd, error="cli_not_found",
                            stderr=f"{self._argv0[0]!r} not on PATH")
        except subprocess.TimeoutExpired:
            return TvResult(False, cmd, error="timeout", stderr="timed out")
        data = None
        try:
            data = json.loads(p.stdout) if p.stdout.strip() else None
        except json.JSONDecodeError:
            data = None
        return TvResult(p.returncode == 0, cmd, data, p.stdout, p.stderr, p.returncode)

    # ---- read-only / navigation / replay / draw convenience (thin; no interpretation) ----
    # NOTE: real CLI verb for the CDP health check is `status` (there is no `health_check`).
    def health(self) -> TvResult: return self.run("status")
    def status(self) -> TvResult: return self.run("status")
    def help(self, *sub) -> TvResult: return self.run(*sub, "--help")

    def set_symbol(self, symbol: str) -> TvResult: return self.run("symbol", symbol)
    def set_timeframe(self, tf: str) -> TvResult: return self.run("timeframe", tf)
    def scroll_to_date(self, iso: str) -> TvResult: return self.run("scroll", "--date", iso)
    def set_visible_range(self, frm: int, to: int) -> TvResult:
        return self.run("range", "--from", int(frm), "--to", int(to))

    def ohlcv(self, summary: bool = False) -> TvResult:
        return self.run("ohlcv", *(["--summary"] if summary else []))
    def quote(self) -> TvResult: return self.run("quote")
    def study_values(self) -> TvResult: return self.run("values")   # top-level verb, not `data values`
    def pine_lines(self, study: Optional[str] = None) -> TvResult:
        return self.run("data", "lines", *(["--filter", study] if study else []))
    def pine_labels(self, study: Optional[str] = None) -> TvResult:
        return self.run("data", "labels", *(["--filter", study] if study else []))
    def pine_boxes(self, study: Optional[str] = None) -> TvResult:
        return self.run("data", "boxes", *(["--filter", study] if study else []))

    def replay_start(self, iso: str) -> TvResult: return self.run("replay", "start", "--date", iso)
    def replay_step(self) -> TvResult: return self.run("replay", "step")
    def replay_status(self) -> TvResult: return self.run("replay", "status")
    def replay_stop(self) -> TvResult: return self.run("replay", "stop")

    def draw_list(self) -> TvResult: return self.run("draw", "list")
    def draw_clear(self) -> TvResult: return self.run("draw", "clear")
    def screenshot(self, region: str = "chart") -> TvResult:
        return self.run("screenshot", "-r", region)
