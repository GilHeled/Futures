from mnq_system.config import CONTRACT_SPECS, ContractSpec


def test_contract_specs_has_correct_tick_size_and_point_value_per_symbol():
    assert CONTRACT_SPECS["MNQ"] == ContractSpec(symbol="MNQ", tick_size=0.25, point_value=2.0)
    assert CONTRACT_SPECS["MES"] == ContractSpec(symbol="MES", tick_size=0.25, point_value=5.0)
    assert CONTRACT_SPECS["MYM"] == ContractSpec(symbol="MYM", tick_size=1.0, point_value=0.5)
    assert CONTRACT_SPECS["M2K"] == ContractSpec(symbol="M2K", tick_size=0.10, point_value=5.0)


def test_contract_specs_keys_match_their_own_symbol_field():
    for key, spec in CONTRACT_SPECS.items():
        assert spec.symbol == key
