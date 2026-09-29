import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { defaultDocument, missingKeys } from "@/features/config/types"
import type { Schemas } from "@/lib/api/types"

const CARD_ID = "0a1b2c3d-0000-4000-8000-000000000001"

export const cardFixture: Schemas["CardOut"] = {
  id: CARD_ID,
  messageId: "1234567890123456789", // Discord snowflake: string, never a number
  signalId: "0a1b2c3d-0000-4000-8000-0000000000e2",
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

export const warningAlertFixture: Schemas["AlertOut"] = {
  id: "0a1b2c3d-0000-4000-8000-0000000000a2",
  kind: "parser_degraded",
  severity: "warning",
  message: "Parser degraded for author 111",
  acknowledged: false,
  cardId: CARD_ID,
  createdAt: "2026-09-29T14:02:00Z",
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

export const positionDetailFixture: Schemas["PositionDetail"] = {
  ...positionFixture,
  events: [
    { at: "2026-09-29T14:00:00Z", change: "opened", before: null, after: { status: "open" } },
    { at: "2026-09-29T14:30:00Z", change: "trimmed", before: { remainingFraction: "1" }, after: { remainingFraction: "0.5" } },
  ],
  links: [
    { signalId: "0a1b2c3d-0000-4000-8000-0000000000e1", method: "reply_chain", confidence: "0.95", outcome: "linked", createdAt: "2026-09-29T14:00:05Z" },
  ],
  lots: [lotFixture],
}

export const lotDetailFixture: Schemas["LotDetail"] = {
  ...lotFixture,
  events: [],
  orders: [orderFixture],
  stops: [
    { id: "0a1b2c3d-0000-4000-8000-0000000000f1", basis: "price", heldBy: "system", kind: "stop", level: "0.90", qty: 1, status: "active" },
  ],
}

export const orderDetailFixture: Schemas["OrderDetail"] = {
  ...orderFixture,
  events: [
    { at: "2026-09-29T14:01:00Z", source: "system", status: "intent", brokerPayload: null },
    { at: "2026-09-29T14:01:02Z", source: "broker", status: "submitted", brokerPayload: null },
    { at: "2026-09-29T14:01:05Z", source: "broker", status: "partially_filled", brokerPayload: null },
  ],
  fills: [
    { id: "0a1b2c3d-0000-4000-8000-0000000000a9", orderId: orderFixture.id, price: "1.25", qty: 1, filledAt: "2026-09-29T14:01:05Z" },
  ],
}

export const profileFixture: Schemas["ProfileOut"] = {
  aliases: ["spy", "spx"],
  lexicon: ["lotto"],
  habits: [],
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
  oldestUnprocessedSeconds: 4.5,
  backlogDepth: 7,
  catchUp: false,
  invariantBlocks: 2,
  flags: {},
  cardOutcomes: [{ toState: "approved", reasonCode: "user_approved", count: 5 }],
  latency: [
    { stage: "total", path: "fast", count: 40, p50Ms: 120, p95Ms: 340.5, p99Ms: 900 },
    { stage: "interpret", path: "slow", count: 8, p50Ms: 1500, p95Ms: 3200, p99Ms: 4100 },
  ],
  queueWait: [{ providerId: "111222333444555666", count: 12, p95Ms: 85, maxMs: 210 }],
  llm: {
    models: [
      {
        provider: "anthropic",
        model: "claude-haiku",
        calls: 30,
        latencyP50Ms: 700,
        latencyP95Ms: 1800,
        outcomes: { ok: 27, timeout: 3 },
        errorRate: 0.1,
        fallbackCalls: 4,
        spendUsd: "1.2345",
      },
    ],
    providers: [
      { provider: "anthropic", breakerOpen: true, slotsSinceStart: 30, semaphoreWaitsSinceStart: 2, rateLimitedSinceStart: 1 },
    ],
    monthlyBudgetUsd: "50",
    slowPathShare: 0.2,
    spendMonthToDateUsd: "12.5",
  },
  messages: { backfilled: 3, duplicatesSinceStart: 6, failed: 1, late: 2, stale: 4, total: 48 },
  orders: { byStatus: { filled: 3, partially_filled: 1 }, rejected: 2, reprices: 5 },
  postgres: { connections: 9, lockWaits: 0 },
  reconciliation: { clean: false, error: "broker timeout", lastRunAt: "2026-09-29T14:10:00Z", mismatchAlerts: 1, mismatches: 3, unknownOrders: 2 },
} satisfies Schemas["HealthDetails"]

const configDocument = {
  ...defaultDocument(),
  channels: ["999888777666555444"],
  feed_silence_s: 60,
  option_tiers: { default: "500", small: "250", lotto: "100" },
  entry_chase_pct: "2",
}

/** Version 3, money values unset: `missingForEntries` lists them. */
export const configFixture: Schemas["ConfigResponse"] = {
  version: 3,
  document: configDocument,
  missingForEntries: missingKeys(configDocument),
}

export const configChangesFixture: Schemas["Page_ConfigChangeOut_"] = {
  items: [
    { version: 3, actor: "alice", reason: "tighten chase", diff: { entry_chase_pct: { before: "3", after: "2" } }, at: "2026-09-29T14:00:00Z" },
    { version: 2, actor: "bob", reason: null, diff: { channels: { before: [], after: ["999888777666555444"] }, feed_silence_s: { before: null, after: 60 } }, at: "2026-09-28T14:00:00Z" },
  ],
  nextCursor: null,
}

export const queueItemFixture: Schemas["QueueItem"] = {
  eventKey: "1234567890123456789.create.0",
  providerId: "1",
  postedAt: "2026-09-29T14:00:00Z",
  text: "BTO SPY 550c 1.20",
  context: [
    { id: "11", atEt: "09:55", text: "earlier chatter", isReplyParent: false },
    { id: "12", atEt: "09:58", text: "watching SPY", isReplyParent: true },
  ],
  prelabel: {
    signals: [{ idx: 0, msgType: "entry", instrument: "option", fields: { underlying: "SPY", strike: "550", right: "call", entry_price: "1.20" } }],
    link: null,
  },
}

export const labelProgressFixture: Schemas["LabelProgress"] = {
  actionable: 12,
  nonActionable: 30,
  providers: 2,
  minActionable: 500,
  minNonActionable: 2000,
  minProviders: 5,
}

export const handlers = [
  http.get("*/api/v1/config", () => HttpResponse.json(configFixture)),
  http.put("*/api/v1/config", async ({ request }) => {
    const body = (await request.json()) as { document: Record<string, unknown> }
    return HttpResponse.json({ version: configFixture.version + 1, document: body.document, missingForEntries: missingKeys(body.document) })
  }),
  http.get("*/api/v1/config/changes", () => HttpResponse.json(configChangesFixture)),
  http.get("*/api/v1/cards", ({ request }) => {
    const state = new URL(request.url).searchParams.get("state")
    return HttpResponse.json(page(state && state !== cardFixture.state ? [] : [cardFixture]))
  }),
  http.get("*/api/v1/alerts", ({ request }) => {
    const params = new URL(request.url).searchParams
    const severity = params.get("severity")
    const acknowledged = params.get("acknowledged")
    return HttpResponse.json(
      page(
        [alertFixture, warningAlertFixture].filter(
          (alert) =>
            (!severity || alert.severity === severity) &&
            (acknowledged == null || String(alert.acknowledged) === acknowledged),
        ),
      ),
    )
  }),
  http.get("*/api/v1/orders", () => HttpResponse.json(page([orderFixture]))),
  http.get("*/api/v1/orders/:id", () => HttpResponse.json(orderDetailFixture)),
  http.get("*/api/v1/lots", () => HttpResponse.json(page([lotFixture]))),
  http.get("*/api/v1/lots/:id", () => HttpResponse.json(lotDetailFixture)),
  http.get("*/api/v1/positions", () => HttpResponse.json(page([positionFixture]))),
  http.get("*/api/v1/positions/:id", () => HttpResponse.json(positionDetailFixture)),
  http.get("*/api/v1/providers/:id/profile", () => HttpResponse.json(profileFixture)),
  http.get("*/api/v1/labels/queue", () => HttpResponse.json(page([queueItemFixture]))),
  http.get("*/api/v1/labels/progress", () => HttpResponse.json(labelProgressFixture)),
  http.put("*/api/v1/labels/:eventKey", async ({ request, params }) => {
    const body = (await request.json()) as { label: Schemas["Label"] }
    return HttpResponse.json({ ...queueItemFixture, eventKey: params.eventKey, labels: [], prelabel: body.label })
  }),
  http.get("*/api/v1/feed", () => HttpResponse.json(page([feedFixture]))),
  http.get("*/api/v1/health/details", ({ request }) =>
    HttpResponse.json({ ...healthFixture, window: new URL(request.url).searchParams.get("window") }),
  ),
]

export const server = setupServer(...handlers)
