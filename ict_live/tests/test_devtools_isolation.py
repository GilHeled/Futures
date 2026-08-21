"""Enforce the one-way dependency rule: devtools/ may import the engine, but NO engine module
may import devtools/ (or the MCP client). The TradingView MCP is a dev tool, never a runtime
dependency of the strategy engine. This test is the mechanical guard behind that promise.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # ict_live/
ENGINE_DIRS = ["market", "feeds", "storage", "structure"]
BANNED = ("devtools", "tvmcp")


def _engine_py_files():
    files = [ROOT / "config.py"]
    for d in ENGINE_DIRS:
        files += (ROOT / d).rglob("*.py")
    return [f for f in files if f.exists() and "__pycache__" not in f.parts]


def _imports(path: Path):
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                yield n.name
        elif isinstance(node, ast.ImportFrom):
            yield node.module or ""


def test_engine_never_imports_devtools():
    offenders = []
    for f in _engine_py_files():
        for mod in _imports(f):
            if any(b in mod.split(".") for b in BANNED):
                offenders.append((str(f.relative_to(ROOT)), mod))
    assert not offenders, f"engine modules must not import devtools/tvmcp: {offenders}"


def test_devtools_is_allowed_to_import_engine():
    # sanity: the client/probe exist and importing them does not drag in a web/MCP dependency
    from ict_live.devtools.tvmcp.client import TvClient
    tv = TvClient(binary="definitely-not-a-real-binary")
    assert tv.available() is False
    r = tv.health()
    assert r.ok is False and r.error == "cli_not_found"   # degrades cleanly, never raises
