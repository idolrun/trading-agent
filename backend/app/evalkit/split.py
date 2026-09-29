"""Deterministic dev/test split of eval labels by trading day (C67): a day is never in both."""

import hashlib
from datetime import date

from app.evalkit.models import Split


def split_for(day: date) -> Split:
    """About one day in five is a test day; the same day always lands in the same split."""
    return (
        Split.TEST
        if int(hashlib.sha256(day.isoformat().encode()).hexdigest(), 16) % 5 == 0
        else Split.DEV
    )
