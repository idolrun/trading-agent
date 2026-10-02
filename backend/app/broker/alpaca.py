"""Alpaca broker adapter (F051, OD-3): `BrokerClient` + `BrokerEvents` for the paper (or live)
account selected in settings.

- `client_order_id` is Alpaca's own field: unique per account, server-enforced, with a lookup
  endpoint, so a duplicate submit returns the existing order like `FakeBroker` does.
- Options are long-only here: BUY = buy_to_open, SELL = sell_to_close.
- Fills arrive on the `trade_updates` WebSocket; each carries a unique `execution_id`.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from decimal import Decimal
from typing import Any

import websockets

from app.alpaca_api import (
    AlpacaApi,
    AlpacaError,
    AlpacaNotSent,
    AlpacaRefused,
    alpaca_symbol,
    dec,
    instrument_of,
)
from app.broker.errors import BrokerRejected, BrokerTimeout, BrokerUnavailable
from app.broker.port import (
    BrokerAccount,
    BrokerEvent,
    BrokerOrder,
    BrokerPosition,
    OcoRequest,
    OrderRequest,
)
from app.execution.models import OrderStatus, OrderType, Side, TimeInForce
from app.instruments import OptionContract
from app.markettime import Clock, SystemClock

logger = logging.getLogger(__name__)

STATUS = {
    "new": OrderStatus.SUBMITTED,
    "accepted": OrderStatus.SUBMITTED,
    "pending_new": OrderStatus.SUBMITTED,
    "accepted_for_bidding": OrderStatus.SUBMITTED,
    "held": OrderStatus.WORKING,
    "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "filled": OrderStatus.FILLED,
    "pending_cancel": OrderStatus.WORKING,  # still live until the cancel lands
    "pending_replace": OrderStatus.WORKING,
    "replaced": OrderStatus.WORKING,
    "stopped": OrderStatus.WORKING,
    "suspended": OrderStatus.WORKING,
    "calculated": OrderStatus.WORKING,
    "canceled": OrderStatus.CANCELLED,
    "expired": OrderStatus.CANCELLED,
    "done_for_day": OrderStatus.CANCELLED,
    "rejected": OrderStatus.REJECTED,
}
TYPES = {
    OrderType.LIMIT: "limit",
    OrderType.STOP: "stop",  # a stop on an option / stop-market on a stock: Alpaca "stop"
    OrderType.STOP_MARKET: "stop",
    OrderType.STOP_LIMIT: "stop_limit",
}
DONE = (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED)
MAX_BACKOFF_S = 30.0
Connect = Callable[[str], AbstractAsyncContextManager[Any]]


class AlpacaBroker:
    def __init__(
        self,
        api: AlpacaApi,
        clock: Clock | None = None,
        connect: Connect | None = None,
        reconnect_s: float = 1.0,
    ) -> None:
        self._api = api
        self._reconnect_s = reconnect_s
        self._clock = clock or SystemClock()
        self._connect = connect or websockets.connect

    # --- BrokerClient ---

    async def submit(self, req: OrderRequest) -> BrokerOrder:
        try:
            return _order(await self._api.post("/v2/orders", _order_body(req)))
        except AlpacaRefused as exc:
            if exc.status == 422 and "client_order_id" in str(exc):  # a retry of the same intent
                existing = await self.get_by_client_id(req.client_order_id)
                if existing is not None:
                    return existing
            raise BrokerRejected(str(exc)) from exc
        except AlpacaError as exc:
            raise _failure(exc) from exc

    async def submit_oco(self, req: OcoRequest) -> list[BrokerOrder]:
        """Not used: stop and take-profit legs are linked by the system (OD-30), one order each.
        ponytail: native OCO (`order_class: "oco"`) when something needs it and the paper suite
        proves it on options."""
        raise BrokerRejected("native OCO is not implemented; brackets are system-linked (OD-30)")

    async def get_by_client_id(self, client_order_id: str) -> BrokerOrder | None:
        try:
            raw = await self._api.get(
                "/v2/orders:by_client_order_id", client_order_id=client_order_id
            )
        except AlpacaRefused as exc:
            if exc.status == 404:
                return None
            raise BrokerRejected(str(exc)) from exc
        except AlpacaError as exc:
            raise _failure(exc) from exc
        return _order(raw)

    async def cancel(self, broker_order_id: str) -> None:
        """A fill can land between the decision to cancel and the call: Alpaca then answers 422
        "not cancelable". An order that is already done is a successful cancel (as `FakeBroker`
        treats it); the fill itself arrives on the stream. Anything else is refused."""
        try:
            await self._api.delete(f"/v2/orders/{broker_order_id}")
        except AlpacaRefused as exc:
            if exc.status == 422 and await self._is_done(broker_order_id):
                return
            raise BrokerRejected(str(exc)) from exc
        except AlpacaError as exc:
            raise _failure(exc) from exc

    async def _is_done(self, broker_order_id: str) -> bool:
        try:
            return _order(await self._api.get(f"/v2/orders/{broker_order_id}")).status in DONE
        except (AlpacaError, KeyError, ValueError, TypeError):
            return False  # cannot tell: report the refusal

    async def open_orders(self) -> list[BrokerOrder]:
        raw = await self._api.get("/v2/orders", status="open", limit=500, nested=False)
        return _parsed(raw, _order)

    async def positions(self) -> list[BrokerPosition]:
        return [p for p in _parsed(await self._api.get("/v2/positions"), _position) if p.qty != 0]

    async def account(self) -> BrokerAccount:
        raw = await self._api.get("/v2/account")
        return BrokerAccount(
            equity=dec(raw.get("equity")) or Decimal(0),
            buying_power=dec(raw.get("buying_power")) or Decimal(0),
            cash=dec(raw.get("cash")),
            options_buying_power=dec(raw.get("options_buying_power")),
        )

    # --- BrokerEvents ---

    async def events(self) -> AsyncIterator[BrokerEvent]:
        """`trade_updates`, reconnecting forever. What happened while disconnected is startup
        reconciliation's job (F046): the stream has no replay."""
        backoff = self._reconnect_s
        while True:
            try:
                async with self._connect(self._api.stream_url) as socket:
                    await socket.send(json.dumps(self._api.stream_auth()))
                    await socket.send(
                        json.dumps({"action": "listen", "data": {"streams": ["trade_updates"]}})
                    )
                    async for frame in socket:
                        message = json.loads(frame)
                        if message.get("stream") == "listening":  # authorized and subscribed
                            backoff = self._reconnect_s
                        try:
                            events = self._events_of(message)
                        except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
                            # one unreadable update (a symbol we cannot model) must not drop the
                            # stream; reconciliation (F046) sees what it carried
                            logger.warning("alpaca trade update skipped: %r", exc)
                            continue
                        for event in events:
                            yield event
            except Exception as exc:  # a dropped stream costs latency, never an event
                logger.warning("alpaca trade stream dropped: %s", exc)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_S)

    def _events_of(self, message: dict[str, Any]) -> list[BrokerEvent]:
        data = message.get("data") or {}
        if message.get("stream") == "authorization" and data.get("status") != "authorized":
            raise ConnectionError(f"alpaca stream authorization failed: {data}")
        if message.get("stream") != "trade_updates":
            return []
        order = _order(data["order"])
        at = _time(data.get("timestamp")) or self._clock.now()
        common: dict[str, Any] = {
            "broker_order_id": order.broker_order_id,
            "client_order_id": order.client_order_id,
            "status": order.status,
            "at": at,
        }
        if data["event"] in ("fill", "partial_fill"):
            return [
                BrokerEvent(
                    kind="fill",
                    fill_id=data["execution_id"],
                    qty=int(Decimal(str(data["qty"]))),
                    price=Decimal(str(data["price"])),
                    **common,
                )
            ]
        return [BrokerEvent(kind="status", **common)]


def _parsed[T](rows: list[dict[str, Any]], parse: Callable[[dict[str, Any]], T]) -> list[T]:
    """Parse a listing, skipping rows we cannot model (a crypto pair, an odd symbol in the
    account): one foreign row must not fail the whole read and block reconciliation."""
    parsed = []
    for row in rows:
        try:
            parsed.append(parse(row))
        except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
            logger.warning("alpaca row skipped (%s): %r", row.get("symbol"), exc)
    return parsed


def _position(raw: dict[str, Any]) -> BrokerPosition:
    # whole contracts and shares only; a fractional foreign holding truncates (the app never
    # places fractional orders)
    qty = int(Decimal(str(raw["qty"])))
    return BrokerPosition(instrument_of(raw["symbol"]), qty, dec(raw.get("avg_entry_price")))


def _failure(exc: AlpacaError) -> Exception:
    return (
        BrokerUnavailable(str(exc)) if isinstance(exc, AlpacaNotSent) else BrokerTimeout(str(exc))
    )


def _order(raw: dict[str, Any]) -> BrokerOrder:
    status = STATUS.get(str(raw.get("status")))
    if status is None:
        logger.warning("unknown alpaca order status %r", raw.get("status"))
        status = OrderStatus.UNKNOWN
    filled = int(Decimal(str(raw.get("filled_qty") or 0)))
    return BrokerOrder(
        broker_order_id=str(raw["id"]),
        client_order_id=raw.get("client_order_id") or "",
        instrument=instrument_of(raw["symbol"]),
        side=Side(raw["side"]),
        qty=int(Decimal(str(raw["qty"]))),
        status=status,
        filled_qty=filled,
        avg_fill_price=dec(raw.get("filled_avg_price")) if filled else None,
    )


def _time(stamp: str | None) -> datetime | None:
    return datetime.fromisoformat(stamp) if stamp else None


def _order_body(req: OrderRequest) -> dict[str, Any]:
    body: dict[str, Any] = {
        "symbol": alpaca_symbol(req.instrument),
        "qty": str(req.qty),
        "side": req.side.value,
        "type": TYPES[req.order_type],
        "time_in_force": "gtc" if req.tif == TimeInForce.GTC else "day",
        "client_order_id": req.client_order_id,
    }
    if req.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT):
        body["limit_price"] = format(req.limit_price, "f")
    if req.order_type != OrderType.LIMIT:
        body["stop_price"] = format(req.stop_price, "f")
    if isinstance(req.instrument, OptionContract):
        body["position_intent"] = "buy_to_open" if req.side == Side.BUY else "sell_to_close"
    return body
