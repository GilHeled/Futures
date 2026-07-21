from types import SimpleNamespace

from mnq_system.cli import _build_account_config, _resolve_contract_spec, _resolve_symbol
from mnq_system.config import ContractSpec


def _args(**overrides):
    defaults = dict(symbol=None, provider="databento", risk_pct=None)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_resolve_symbol_falls_back_to_provider_default_when_not_given():
    assert _resolve_symbol(_args(provider="databento")) == "MNQ"
    assert _resolve_symbol(_args(provider="yfinance")) == "MNQ=F"


def test_resolve_symbol_prefers_explicit_override():
    assert _resolve_symbol(_args(symbol="MES")) == "MES"


def test_resolve_contract_spec_looks_up_known_symbols():
    assert _resolve_contract_spec("MES").tick_size == 0.25
    assert _resolve_contract_spec("MES").point_value == 5.0
    assert _resolve_contract_spec("MYM").tick_size == 1.0
    assert _resolve_contract_spec("MYM").point_value == 0.5


def test_resolve_contract_spec_strips_provider_suffix_and_is_case_insensitive():
    assert _resolve_contract_spec("mnq=F").symbol == "MNQ"


def test_resolve_contract_spec_falls_back_to_default_for_unknown_symbol():
    assert _resolve_contract_spec("ZZZZ") == ContractSpec()


def test_build_account_config_sets_the_correct_contract_for_a_different_symbol():
    account = _build_account_config(_args(symbol="MES"))

    assert account.contract.symbol == "MES"
    assert account.contract.tick_size == 0.25
    assert account.contract.point_value == 5.0


def test_build_account_config_still_honors_risk_pct_override():
    account = _build_account_config(_args(symbol="MES", risk_pct=0.01))

    assert account.risk.risk_pct_per_trade == 0.01
    assert account.contract.symbol == "MES"


def test_build_account_config_defaults_to_mnq_when_no_symbol_given():
    account = _build_account_config(_args())

    assert account.contract.symbol == "MNQ"
