import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import type { Schemas } from "@/lib/api/types"

const CARD_ID = "0a1b2c3d-0000-4000-8000-000000000001"

export const cardFixture: Schemas["CardOut"] = {
  id: CARD_ID,
  messageId: "1234567890123456789", // Discord snowflake: string, never a number
  signalIndex: 0,
  action: "entry",
  cardType: "normal",
  state: "pending",
  providerId: "1",
  providerPositionId: null,
  lotId: null,
  underlying: "SPY",
  contract: { instrument: "option", underlying: "SPY", expiry: "2026-10-16", strike: "550", right: "call" },
  proposal: {
    qty: 2,
    price: "1.25",
    signalPrice: "1.20",
    warnings: ["Signal is 4 minutes old"],
    labels: [],
    missingFields: [],
    reviewReasons: [],
    candidatePositions: [],
    tpLegs: [],
    stop: null,
    breakevenOptions: null,
    disabledReason: null,
    suggestedUnderlying: null,
  },
  inferredFields: [],
  sourceMessages: [],
  note: null,
  noteHidden: false,
  postedAt: "2026-09-29T14:00:00Z",
  validUntil: "2026-09-29T14:05:00Z",
  version: 1,
}

const page = <T>(items: T[]) => ({ items, nextCursor: null })

export const alertFixture: Schemas["AlertOut"] = {
  id: "0a1b2c3d-0000-4000-8000-0000000000a1",
  kind: "feed_silent",
  severity: "urgent",
  message: "Feed silent for 300s",
  acknowledged: false,
  cardId: null,
  createdAt: "2026-09-29T14:00:00Z",
}

export const orderFixture: Schemas["OrderOut"] = {
  id: "0a1b2c3d-0000-4000-8000-0000000000b1",
  cardId: CARD_ID,
  lotId: null,
  kind: "entry",
  status: "partially_filled",
  side: "buy",
  qty: 2,
  filledQty: 1,
  limitPrice: "1.25",
  stopPrice: null,
  brokerSymbol: "SPY261016C00550000",
  clientOrderId: "cid-1",
  mode: "shadow",
  autoReason: null,
  createdAt: "2026-09-29T14:01:00Z",
}

export const lotFixture: Schemas["LotOut"] = {
  id: "0a1b2c3d-0000-4000-8000-0000000000c1",
  providerId: "1",
  providerPositionId: null,
  contract: cardFixture.contract!,
  qty: 1,
  avgPrice: "1.25",
  avgPriceLabel: "system average",
  isForeign: false,
  status: "open",
  openedAt: "2026-09-29T14:02:00Z",
}

export const positionFixture: Schemas["PositionOut"] = {
  id: "0a1b2c3d-0000-4000-8000-0000000000d1",
  providerId: "1",
  underlying: "SPY",
  instrument: "option",
  expiry: "2026-10-16",
  strike: "550",
  optionRight: "call",
  status: "open",
  isCorrected: false,
  openedAt: "2026-09-29T14:00:00Z",
  providerEntryPrice: "1.20",
  remainingFraction: "1",
  stopBasis: null,
  stopLevel: null,
  tpLevels: [],
}

export const feedFixture: Schemas["FeedItem"] = {
  messageId: "1234567890123456789",
  eventType: "create",
  eventSeq: 0,
  providerId: "111222333444555666",
  channelId: "999888777666555444",
  postedAt: "2026-09-29T14:00:00Z",
  text: "BTO SPY 550c 1.20",
  replyToId: null,
  status: "done",
  labels: [],
  msgTypes: ["entry"],
  cardIds: [CARD_ID],
}

export const healthFixture = {
  status: "ok",
  window: "session",
  feedSilenceSeconds: 12,
  feedHeartbeatAgeSeconds: 3,
  heartbeatAgeSeconds: 2,
  oldestUnprocessedSeconds: null,
  backlogDepth: 0,
  catchUp: false,
  invariantBlocks: 0,
  flags: {},
  cardOutcomes: [],
  latency: [],
  queueWait: [],
  llm: { models: [], providers: [], monthlyBudgetUsd: null, slowPathShare: null, spendMonthToDateUsd: "0" },
  messages: { backfilled: 0, duplicatesSinceStart: 0, failed: 0, late: 0, stale: 0, total: 0 },
  orders: { byStatus: {}, rejected: 0, reprices: 0 },
  postgres: { connections: 1, lockWaits: 0 },
  reconciliation: { clean: true, error: null, lastRunAt: null, mismatchAlerts: 0, mismatches: 0, unknownOrders: 0 },
} satisfies Schemas["HealthDetails"]

export const handlers = [
  http.get("*/api/v1/cards", ({ request }) => {
    const state = new URL(request.url).searchParams.get("state")
    return HttpResponse.json(page(state && state !== cardFixture.state ? [] : [cardFixture]))
  }),
  http.get("*/api/v1/alerts", () => HttpResponse.json(page([alertFixture]))),
  http.get("*/api/v1/orders", () => HttpResponse.json(page([orderFixture]))),
  http.get("*/api/v1/lots", () => HttpResponse.json(page([lotFixture]))),
  http.get("*/api/v1/positions", () => HttpResponse.json(page([positionFixture]))),
  http.get("*/api/v1/feed", () => HttpResponse.json(page([feedFixture]))),
  http.get("*/api/v1/health/details", () => HttpResponse.json(healthFixture)),
]

export const server = setupServer(...handlers)
