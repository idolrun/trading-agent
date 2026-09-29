from datetime import date, timedelta

from app.evalkit.models import Split
from app.evalkit.split import split_for


def test_split_is_deterministic_and_about_one_day_in_five_is_test() -> None:
    days = [date(2026, 1, 2) + timedelta(days=i) for i in range(500)]
    splits = [split_for(d) for d in days]
    assert splits == [split_for(d) for d in days]
    assert 60 < splits.count(Split.TEST) < 140  # ~100 of 500
    assert split_for(date(2026, 9, 28)) == split_for(date(2026, 9, 28))
