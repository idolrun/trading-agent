import { applyCardDecision } from "@/features/cards/decision"
import type { Card, CardDecision, CardListFilters } from "@/features/cards/types"
import { alertMatchesFilters, type Alert, type AlertListFilters } from "@/features/alerts/types"
import type { FeedHealth } from "@/features/health/types"
import type { Lot, LotListFilters } from "@/features/lots/types"
import type { Order, OrderKind, OrderListFilters } from "@/features/orders/types"
import type { PositionListFilters, ProviderPosition } from "@/features/positions/types"

export class StoreError extends Error {
  readonly status: number
  readonly code: string

  constructor(message: string, status: number, code: string) {
    super(message)
    this.name = "StoreError"
    this.status = status
    this.code = code
  }
}

const cards: Card[] = [
  {
    id: "card_spx_entry",
    messageId: "msg_1001",
    signalIndex: 0,
    action: "entry",
    cardType: "normal",
    state: "pending",
    providerId: "alpha",
    underlying: "SPXW",
    proposal: { qty: 2, price: 4.5, warnings: [] },
    validUntil: "2026-09-28T20:15:00.000Z",
    lotId: null,
  },
  {
    id: "card_nvda_trim",
    messageId: "msg_1002",
    signalIndex: 0,
    action: "trim",
    cardType: "normal",
    state: "pending",
    providerId: "alpha",
    underlying: "NVDA",
    proposal: { qty: null, price: 6.2, warnings: ["Quantity is required"] },
    validUntil: "2026-09-28T20:20:00.000Z",
    lotId: "lot_nvda",
  },
  {
    id: "card_qqq_exit",
    messageId: "msg_1003",
    signalIndex: 0,
    action: "exit",
    cardType: "inferred",
    state: "pending",
    providerId: "beta",
    underlying: "QQQ",
    proposal: { qty: 1, price: 3.1, warnings: [] },
    validUntil: "2026-09-28T20:25:00.000Z",
    lotId: "lot_qqq",
  },
  {
    id: "card_iwm_entry",
    messageId: "msg_1004",
    signalIndex: 0,
    action: "entry",
    cardType: "disabled",
    state: "disabled",
    providerId: "alpha",
    underlying: "IWM",
    proposal: { qty: 1, price: 2.1, warnings: ["Tier is below the minimum"] },
    validUntil: "2026-09-28T20:10:00.000Z",
    lotId: null,
  },
]

const orders: Order[] = [
  {
    id: "ord_tsla_working",
    cardId: null,
    lotId: "lot_tsla",
    kind: "entry",
    status: "working",
    clientOrderId: "card_tsla_entry",
  },
]

const lots: Lot[] = [
  {
    id: "lot_nvda",
    providerPositionId: "pos_nvda",
    contract: "NVDA 2026-10-16 140 C",
    qty: 4,
    avgPrice: 5.8,
    isForeign: false,
    status: "open",
  },
  {
    id: "lot_qqq",
    providerPositionId: "pos_qqq",
    contract: "QQQ 2026-10-16 480 P",
    qty: 1,
    avgPrice: 3.4,
    isForeign: false,
    status: "open",
  },
  {
    id: "lot_tsla",
    providerPositionId: "pos_tsla",
    contract: "TSLA 2026-10-16 250 C",
    qty: 2,
    avgPrice: 8.1,
    isForeign: false,
    status: "open",
  },
]

const positions: ProviderPosition[] = [
  {
    id: "pos_nvda",
    providerId: "alpha",
    instrument: "option",
    underlying: "NVDA",
    expiry: "2026-10-16",
    strike: 140,
    optionRight: "call",
    status: "open",
  },
  {
    id: "pos_qqq",
    providerId: "beta",
    instrument: "option",
    underlying: "QQQ",
    expiry: "2026-10-16",
    strike: 480,
    optionRight: "put",
    status: "trimmed",
  },
  {
    id: "pos_tsla",
    providerId: "alpha",
    instrument: "option",
    underlying: "TSLA",
    expiry: "2026-10-16",
    strike: 250,
    optionRight: "call",
    status: "open",
  },
]

const alerts: Alert[] = [
  {
    id: "alert_reconcile",
    severity: "urgent",
    message: "Broker quantity for TSLA differs from the lot.",
    acknowledged: false,
    cardId: null,
    createdAt: "2026-09-28T18:00:00.000Z",
  },
  {
    id: "alert_late",
    severity: "info",
    message: "A late NVDA message was flagged for review.",
    acknowledged: false,
    cardId: "card_nvda_trim",
    createdAt: "2026-09-28T18:05:00.000Z",
  },
]

const health: FeedHealth = {
  status: "ok",
  feedSilenceSeconds: 4,
}

function missing(label: string): StoreError {
  return new StoreError(`${label} not found`, 404, "not_found")
}

function requireOne<T extends { id: string }>(items: T[], id: string, label: string): T {
  const found = items.find((item) => item.id === id)
  if (!found) throw missing(label)
  return found
}

function orderKind(card: Card): OrderKind {
  if (card.action === "exit" || card.action === "trim" || card.action === "close") return "exit"
  if (card.action === "stop") return "stop"
  if (card.action === "tp") return "tp"
  return "entry"
}

export function listCards(filters: CardListFilters): Card[] {
  return cards.filter((card) => {
    if (filters.state && card.state !== filters.state) return false
    if (filters.providerId && card.providerId !== filters.providerId) return false
    return true
  })
}

export function getCard(cardId: string): Card {
  return requireOne(cards, cardId, "Card")
}

export function decideCard(cardId: string, decision: CardDecision): Card {
  const index = cards.findIndex((card) => card.id === cardId)
  if (index === -1) throw missing("Card")
  const current = cards[index]
  if (current.state !== "pending") {
    throw new StoreError("Card is not pending", 409, "conflict")
  }
  if (
    decision.decision === "approve" &&
    current.action === "trim" &&
    (current.proposal.qty == null || current.proposal.qty <= 0)
  ) {
    throw new StoreError("Trim quantity is required", 422, "invalid")
  }
  const next = applyCardDecision(current, decision)
  cards[index] = next
  if (decision.decision === "approve" && !orders.some((order) => order.clientOrderId === next.id)) {
    orders.unshift({
      id: `ord_${next.id}`,
      cardId: next.id,
      lotId: next.lotId,
      kind: orderKind(next),
      status: "intent",
      clientOrderId: next.id,
    })
  }
  return next
}

export function listOrders(filters: OrderListFilters): Order[] {
  return orders.filter((order) => {
    if (filters.status && order.status !== filters.status) return false
    if (filters.lotId && order.lotId !== filters.lotId) return false
    return true
  })
}

export function getOrder(orderId: string): Order {
  return requireOne(orders, orderId, "Order")
}

export function listLots(filters: LotListFilters): Lot[] {
  return lots.filter((lot) => (filters.status ? lot.status === filters.status : true))
}

export function getLot(lotId: string): Lot {
  return requireOne(lots, lotId, "Lot")
}

export function listPositions(filters: PositionListFilters): ProviderPosition[] {
  return positions.filter((position) => {
    if (filters.status && position.status !== filters.status) return false
    if (filters.providerId && position.providerId !== filters.providerId) return false
    return true
  })
}

export function getPosition(positionId: string): ProviderPosition {
  return requireOne(positions, positionId, "Position")
}

export function getAlert(alertId: string): Alert {
  return requireOne(alerts, alertId, "Alert")
}

export function listAlerts(filters: AlertListFilters): Alert[] {
  return alerts.filter((alert) => alertMatchesFilters(alert, filters))
}

export function acknowledgeAlert(alertId: string): Alert {
  const index = alerts.findIndex((alert) => alert.id === alertId)
  if (index === -1) throw missing("Alert")
  const next = { ...alerts[index], acknowledged: true }
  alerts[index] = next
  return next
}

export function getFeedHealth(): FeedHealth {
  return health
}
