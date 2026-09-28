"""Broker failure modes. The distinction drives recovery (FR-22)."""


class BrokerError(Exception):
    pass


class BrokerTimeout(BrokerError):
    """Outcome unknown: the order may or may not exist. Caller must query by client id."""


class BrokerRejected(BrokerError):
    """The broker received and refused the request."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class BrokerUnavailable(BrokerError):
    """Definitely not sent (e.g. connection refused before the request left)."""
