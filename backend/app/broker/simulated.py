"""Shadow mode broker (OD-28, user-confirmed 2026-09-29): orders are recorded and marketable
ones fill immediately at the live quote, so lots, exits and stops are exercised. It never
calls a real broker."""

from decimal import Decimal

from app.broker.fake import FakeBroker
from app.broker.port import BrokerOrder, OrderRequest
from app.execution.models import OrderType, Side
from app.marketdata.port import MarketData
from app.markettime import Clock


class SimulatedBroker(FakeBroker):
    def __init__(self, marketdata: MarketData, clock: Clock | None = None) -> None:
        super().__init__(clock)
        self._md = marketdata

    async def submit(self, req: OrderRequest) -> BrokerOrder:
        order = await super().submit(req)  # default script: working, never fills by itself
        if order.status != "working" or req.order_type in (OrderType.STOP, OrderType.STOP_MARKET):
            return order  # stops wait for their trigger (F042 / F044)
        price = await self._marketable_price(req)
        return self.fill(order.broker_order_id, price=price) if price is not None else order

    async def _marketable_price(self, req: OrderRequest) -> Decimal | None:
        try:
            quote = await self._md.quote(req.instrument)
        except Exception:
            return None  # no quote: the order stays working, like a real resting order
        if req.side == Side.BUY:
            px = quote.ask
            ok = px is not None and (req.limit_price is None or px <= req.limit_price)
        else:
            px = quote.bid
            ok = px is not None and (req.limit_price is None or px >= req.limit_price)
        return px if ok else None
