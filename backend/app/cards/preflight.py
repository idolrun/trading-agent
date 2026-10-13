"""What revalidation looks at, as data (PRD 7.2, C22, FR-18, FR-20).

`ApprovalService.preflight` fills one of these from fresh quote, account, positions, working
orders and risk limits. Revalidation after an approval fails on the first check that is not a
pass; the dashboard shows the same list read-only before the user decides. Nothing here decides
anything: the checks are computed by code (`cards.validity`, `decide.risk`, the ledger)."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

Status = Literal["pass", "review", "blocked"]


@dataclass(frozen=True)
class Check:
    name: str  # hours | validity | quote | quote_fresh | quote_valid | price_band | risk | ...
    status: Status
    reason: str | None = None  # the stable code revalidation fails with ("price_band", ...)
    detail: str | None = None


@dataclass
class Preflight:
    applicable: bool = True  # False: nothing to check (a review card, a card already decided)
    checks: list[Check] = field(default_factory=list)
    qty: int | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    last: Decimal | None = None
    quote_at: datetime | None = None
    signal_price: Decimal | None = None
    limit_price: Decimal | None = None
    est_cost: Decimal | None = None  # qty x limit x multiplier (entries), proceeds for exits: n/a
    equity: Decimal | None = None
    cash: Decimal | None = None
    buying_power: Decimal | None = None
    projected_buying_power: Decimal | None = None
    exposure: Decimal | None = None  # open lots + unfilled working entries
    working_exposure: Decimal | None = None
    projected_exposure: Decimal | None = None
    max_exposure: Decimal | None = None
    open_positions: int | None = None
    max_open_positions: int | None = None
    daily_pnl: Decimal | None = None  # realized today + unrealized
    max_daily_loss: Decimal | None = None

    @property
    def failure(self) -> str | None:
        """The first check that is not a pass, as the code revalidation invalidates with."""
        return next((c.reason or c.name for c in self.checks if c.status != "pass"), None)
