"""In-process counters for signals SQL cannot see (PRD §16): duplicate deliveries, provider
semaphore waits and 429s. Reset on restart, which the health payload states."""

from collections import Counter

counts: Counter[str] = Counter()


def bump(key: str) -> None:
    counts[key] += 1


def per_provider(prefix: str) -> dict[str, int]:
    """`bump(f"{prefix}:{provider}")` totals as {provider: n}."""
    return {k.split(":", 1)[1]: n for k, n in counts.items() if k.startswith(f"{prefix}:")}
