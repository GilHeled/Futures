from intraday_alerts.topstep import TopstepRiskState, simulate_alert_sequence


def test_prospective_block_when_worst_case_breaches_daily():
    s = TopstepRiskState()
    # worst case just over the effective daily stop ($800) must be blocked up-front
    assert s.can_enter(799.0) is True
    assert s.can_enter(801.0) is False


def test_daily_halt_after_effective_stop():
    s = TopstepRiskState()
    s.register_exit(-800.0)          # hits effective daily stop
    assert s.day_halted is True
    assert s.can_enter(1.0) is False  # no more entries this session
    s.end_day()
    assert s.day_halted is False      # resets next day


def test_trailing_mll_moves_up_and_locks():
    s = TopstepRiskState()
    assert s.locked is False
    s.register_exit(+2500.0)         # balance 52,500
    s.end_day()                      # eod_hwm -> 52,500; eod_hwm - 2000 = 50,500 >= start
    assert s.locked is True
    # effective floor never below start-region once locked
    assert s._real_floor() == s.start_balance


def test_alert_policy_one_position_cooldown_and_max_per_day():
    pv = 5.0
    # 4 candidates same day; entries overlap within cooldown; max 3/day
    cands = [
        {"entry_pos": 0, "exit_pos": 1, "et_date": "D1", "direction": "long",
         "entry_price": 100.0, "stop_price": 99.0, "exit_price": 101.0},
        {"entry_pos": 1, "exit_pos": 2, "et_date": "D1", "direction": "long",   # inside cooldown of #1
         "entry_price": 101.0, "stop_price": 100.0, "exit_price": 102.0},
        {"entry_pos": 10, "exit_pos": 11, "et_date": "D1", "direction": "long",
         "entry_price": 102.0, "stop_price": 101.0, "exit_price": 103.0},
        {"entry_pos": 20, "exit_pos": 21, "et_date": "D1", "direction": "short",
         "entry_price": 103.0, "stop_price": 104.0, "exit_price": 102.0},
    ]
    realized, report = simulate_alert_sequence(cands, point_value=pv, max_per_day=3, cooldown_bars=3)
    # #2 skipped (cooldown after #1 which exits at pos1, busy_until = 1+3=4)
    entry_positions = [r["entry_pos"] for r in realized]
    assert 1 not in entry_positions
    assert len(realized) <= 3
    assert report["prevented_breaches"] >= 0


def test_prevented_breach_counted():
    pv = 5.0
    # a trade whose worst-case loss ($ = |entry-stop|*pv) exceeds the daily stop is blocked
    cands = [{"entry_pos": 0, "exit_pos": 1, "et_date": "D1", "direction": "long",
              "entry_price": 100.0, "stop_price": 0.0, "exit_price": 101.0}]  # stop 100 pts * $5 = $500? -> below 800
    # make it breach: |100-(-100)|=200 pts * $5 = $1000 > eff daily $800
    cands[0]["stop_price"] = -100.0
    realized, report = simulate_alert_sequence(cands, point_value=pv)
    assert report["n_realized"] == 0 and report["prevented_breaches"] == 1
