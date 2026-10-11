"""config: the device clock and the day boundary."""
from datetime import datetime, time, timedelta

from support import CFG, clean_dirs
from yolk import activity, config


def check_day_boundary():
    clean_dirs()
    machine_today = datetime.now().date()
    real = config.employee_now
    try:
        config.employee_now = lambda cfg: datetime.combine(machine_today + timedelta(days=1), time(1, 0))
        assert config.summary_today(CFG) == machine_today                      # employee's day is ahead
        config.employee_now = lambda cfg: datetime.combine(machine_today - timedelta(days=1), time(23, 0))
        assert config.summary_today(CFG) == machine_today - timedelta(days=1)  # machine's day is ahead
        config.employee_now = lambda cfg: datetime.combine(machine_today + timedelta(days=1), time(1, 0))
        try:  # "today" on the machine has not ended: refused, as the server would 422 it
            activity.summarize_cli(CFG, machine_today.isoformat(), 7, False)
            raise AssertionError("expected SystemExit")
        except SystemExit as e:
            assert "--date" in str(e)
    finally:
        config.employee_now = real
