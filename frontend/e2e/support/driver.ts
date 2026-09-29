import { createHmac } from "node:crypto"
import { readdirSync, readFileSync } from "node:fs"
import path from "node:path"
import { test as base, expect, request as playwrightRequest, type APIRequestContext, type Page } from "@playwright/test"
import { signIn } from "./auth"
import { BACKEND, BACKEND_DIR, HMAC_SECRET, ORIGIN, UA } from "./env"

/**
 * Test driver (TS port of backend/tests/messages.py + tests/integration/pipeline/harness.py):
 * signed Message API webhooks, recorded interpretations, the test-only clock / market /
 * reset routes (ENVIRONMENT=test), and API reads that assert what the database holds.
 */

export const PROVIDER_A = "700000000000000001"
export const PROVIDER_B = "800000000000000002"
export const CHANNEL = "900000000000000003"
export const T0 = new Date("2026-09-28T14:30:00Z") // Monday 10:30 ET, regular hours
export const FRIDAY = new Date("2026-10-02T14:30:00Z") // GOOGL 10/2 expires that day
export const CUTOFF_1 = new Date("2026-10-02T19:30:00Z") // 16:00 ET close - 30 min
export const CUTOFF_2 = new Date("2026-10-02T19:50:00Z") // - 10 min
const DISCORD_EPOCH_MS = BigInt(1420070400000)
const MARKET = path.join(BACKEND_DIR, "tests/fixtures/market")

/** Non-GET /api/* from a test (the browser sends Origin itself). */
export const ORIGIN_HEADERS = { Origin: ORIGIN }

export const seconds = (at: Date, s: number) => new Date(at.getTime() + s * 1000)

/** backend/tests/decide_helpers.py CONFIG, on the E2E channel. */
export const CONFIG = {
  feed_silence_s: 600,
  catch_up_backlog_n: 20,
  context_recent_messages: 30,
  option_tiers: { default: "1000", lotto: "100" },
  stock_tiers: { default: "5000" },
  entry_chase_pct: "5",
  entry_validity_s: 60,
  entry_cancel_after_s: 120,
  exit_card_timeout_s: 600,
  exit_reprice_interval_s: 5,
  exit_reprice_step_pct: "2",
  exit_floor_pct: "20",
  cutoff_1_min_before_close: 30,
  cutoff_2_min_before_close: 10,
  max_daily_loss_usd: "2000",
  max_open_positions: 10,
  max_total_exposure_usd: "20000",
  identity_price_tolerance_pct: "15",
  adjacent_strike_ratio: "0.5",
  missing_underlying_lookback_days: 5,
  llm_monthly_budget_usd: "200",
  channels: [CHANNEL],
}

// --- Message API payloads (provisional contract, app/ingest/adapter.py) ---

export function snowflake(at: Date, seq = 0): string {
  return (((BigInt(at.getTime()) - DISCORD_EPOCH_MS) << BigInt(22)) | BigInt(seq)).toString()
}

export type Message = { id: string; at: Date; author: string; text: string }

function payload(type: string, msg: Message, extra: Record<string, unknown> = {}) {
  return {
    type,
    message: {
      id: msg.id,
      channel_id: CHANNEL,
      author: { id: msg.author },
      content: msg.text,
      timestamp: msg.at.toISOString(),
      edited_timestamp: null,
      reply_to_id: null,
      mentions: [],
      ...extra,
    },
  }
}

/** X-Signature = hex(HMAC-SHA256(secret, "<ts>." + raw body)) — app/ingest/security.py. */
export function signed(body: string, at: Date) {
  const ts = Math.floor(at.getTime() / 1000).toString()
  const signature = createHmac("sha256", HMAC_SECRET).update(`${ts}.`).update(body).digest("hex")
  return { "X-Timestamp": ts, "X-Signature": signature, "Content-Type": "application/json" }
}

// --- ParsedSignals (app/interpret/signal.py); each literal's raw text must be in the message ---

type Field = { value: string; source: { kind: "literal"; raw: string } }
export const lit = (value: string | number, raw?: string): Field => ({
  value: String(value),
  source: { kind: "literal", raw: raw ?? String(value) },
})

export type Signal = Record<string, unknown>

export function optionEntry(
  underlying: string | null,
  strike: string,
  rightRaw: string,
  expiry: string,
  price: string,
  extra: Record<string, unknown> = {},
): Signal {
  return {
    msg_type: "entry",
    instrument: "option",
    fields: {
      ...(underlying ? { underlying: lit(underlying) } : {}),
      strike: lit(strike),
      right: { value: rightRaw.toLowerCase().endsWith("c") ? "call" : "put", source: { kind: "literal", raw: rightRaw } },
      expiry_token: lit(expiry),
      entry_price: lit(price),
      ...extra,
    },
  }
}

export function followUp(msgType: string, target: string | null, fields: Record<string, unknown> = {}): Signal {
  return { msg_type: msgType, fields, link_hint: { target, confidence: "0.93" } }
}

// --- API shapes read by the assertions (subset of openapi.json) ---

export type Card = {
  id: string
  action: string
  cardType: string
  state: string
  providerId: string | null
  providerPositionId: string | null
  lotId: string | null
  underlying: string | null
  version: number
  proposal: {
    qty: number | null
    price: string | null
    tpLegs: { level: string; qty: number }[]
    labels: string[]
    missingFields: string[]
    reviewReasons: string[]
    suggestedUnderlying: string | null
  }
}
export type CardDetail = Card & {
  events: { toState: string; reasonCode: string }[]
  approval: { decision: string; edits: Record<string, unknown>; stepUp: boolean } | null
}
export type Order = {
  id: string
  cardId: string | null
  lotId: string | null
  kind: string
  side: string
  status: string
  qty: number
  filledQty: number
  limitPrice: string | null
  stopPrice: string | null
  mode: string
  autoReason: string | null
  brokerSymbol: string
}
export type Lot = {
  id: string
  providerId: string | null
  providerPositionId: string | null
  contract: { underlying: string; strike: string | null; right: string | null }
  qty: number
  status: string
  isForeign: boolean
}
export type Position = {
  id: string
  providerId: string
  underlying: string
  strike: string | null
  status: string
  isCorrected: boolean
  remainingFraction: string
  stopLevel: string | null
}
export type Alert = { kind: string; severity: string; message: string; cardId: string | null }

export class Driver {
  now = T0

  constructor(
    readonly backend: APIRequestContext,
    readonly page: Page,
  ) {}

  private async call(method: "POST" | "PUT", url: string, data: unknown, headers?: Record<string, string>) {
    const r = await this.backend.fetch(url, { method, data, headers })
    expect(r.ok(), `${method} ${url}: ${r.status()} ${await r.text()}`).toBeTruthy()
    return r
  }

  /** Empty database, fresh config and synthetic market data, app + browser clock at `at`. */
  async reset(at: Date, config: object | null = CONFIG) {
    await this.call("POST", "/api/v1/test/reset", { at: at.toISOString(), config })
    this.now = at
    const fixtures = readdirSync(MARKET)
      .filter((name) => name.endsWith(".json"))
      .map((name) => JSON.parse(readFileSync(path.join(MARKET, name), "utf8")))
    await this.market({ fixtures })
  }

  async market(body: { fixtures?: object[]; quotes?: object[] }) {
    await this.call("POST", "/api/v1/test/market", body)
  }

  /** Move the app clock (timers fire on it) and keep the browser's Date in step. */
  async clockTo(at: Date, { heartbeat = true } = {}) {
    const r = await this.call("POST", "/api/v1/test/clock", { at: at.toISOString() })
    this.now = new Date((await r.json()).now)
    if (heartbeat) await this.heartbeat() // the Message API stays alive unless a test says not
    await this.page.clock.setSystemTime(this.now)
  }

  advance(s: number, options?: { heartbeat?: boolean }) {
    return this.clockTo(seconds(this.now, s), options)
  }

  async heartbeat() {
    await this.backend.post("/ingest/heartbeat", { data: "{}", headers: signed("{}", this.now) }).then(async (r) => {
      expect(r.status(), await r.text()).toBe(204)
    })
  }

  /** Record the interpretation (null: none), then deliver the webhook signed at the app time. */
  async post(text: string, signals: Signal[] | null, opts: { author?: string; at?: Date; seq?: number } = {}) {
    const at = opts.at ?? this.now
    const msg: Message = { id: snowflake(at, opts.seq ?? 0), at, author: opts.author ?? PROVIDER_A, text }
    await this.deliver(payload("message.create", msg), msg.id, signals)
    return msg
  }

  /** A `message.update` of `original` at the app time (event time = edit time, OD-24). */
  async edit(original: Message, text: string, signals: Signal[] | null) {
    const body = payload("message.create", { ...original, text }, { edited_timestamp: this.now.toISOString() })
    body.type = "message.update"
    await this.deliver(body, original.id, signals)
  }

  private async deliver(body: object, messageId: string, signals: Signal[] | null) {
    if (signals) {
      await this.call("PUT", `/api/v1/test/interpretations/${messageId}`, { signals, interpreter: "llm" })
    }
    const raw = JSON.stringify(body)
    const r = await this.backend.post("/ingest", { data: raw, headers: signed(raw, this.now) })
    expect(r.status(), await r.text()).toBe(200)
    expect(await r.json()).toEqual({ accepted: true })
  }

  // --- reads: the signed-in browser session, through the Next proxy like the dashboard ---

  async get<T>(url: string): Promise<T> {
    const r = await this.page.request.get(url)
    expect(r.ok(), `GET ${url}: ${r.status()} ${await r.text()}`).toBeTruthy()
    return (await r.json()) as T
  }

  private items<T>(url: string) {
    return this.get<{ items: T[] }>(url).then((page) => page.items)
  }

  cards(state?: string) {
    return this.items<Card>(`/api/v1/cards?limit=200${state ? `&state=${state}` : ""}`)
  }
  card(id: string) {
    return this.get<CardDetail>(`/api/v1/cards/${id}`)
  }
  orders() {
    return this.items<Order>("/api/v1/orders?limit=200")
  }
  lots() {
    return this.items<Lot>("/api/v1/lots?limit=200")
  }
  positions(providerId?: string) {
    return this.items<Position>(`/api/v1/positions?limit=200${providerId ? `&providerId=${providerId}` : ""}`)
  }
  alerts() {
    return this.items<Alert>("/api/v1/alerts?limit=200")
  }
  async flags() {
    return (await this.get<{ flags: Record<string, boolean> }>("/api/v1/health/details?window=session")).flags
  }

  /** Poll `read` until `found` returns a value (the pipeline is asynchronous). */
  async waitFor<T, R>(read: () => Promise<T>, found: (value: T) => R | undefined | null | false, message: string) {
    let result: R | undefined | null | false
    await expect
      .poll(
        async () => {
          result = found(await read())
          return Boolean(result)
        },
        { message, timeout: 15_000 },
      )
      .toBe(true)
    return result as R
  }

  /** The one card of `action` for `providerId` in `state`, once it exists. */
  cardFor(action: string, opts: { providerId?: string; state?: string } = {}) {
    return this.waitFor(
      () => this.cards(),
      (cards) =>
        cards.find(
          (c) =>
            c.action === action &&
            (opts.providerId === undefined || c.providerId === opts.providerId) &&
            (opts.state === undefined || c.state === opts.state),
        ),
      `a ${opts.state ?? ""} ${action} card${opts.providerId ? ` for ${opts.providerId}` : ""}`,
    )
  }

  /** The open provider position of `underlying` for `providerId`. */
  positionOf(providerId: string, underlying: string) {
    return this.waitFor(
      () => this.positions(providerId),
      (rows) => rows.find((p) => p.underlying === underlying),
      `${providerId}'s ${underlying} position`,
    )
  }

  /** The open lot of `underlying` for `providerId`, once the fill is applied. */
  lotOf(providerId: string | null, underlying: string) {
    return this.waitFor(
      () => this.lots(),
      (rows) => rows.find((l) => l.providerId === providerId && l.contract.underlying === underlying && l.status === "open"),
      `an open ${underlying} lot for ${providerId}`,
    )
  }
}

/** The article of one card on the desk (by its accessible name: author + action + underlying). */
export function cardOnDesk(page: Page, providerId: string, action: string, underlying = "") {
  return page.getByRole("article", { name: `Provider ${providerId}, ${action} ${underlying}`.trim(), exact: true })
}

export const test = base.extend<{ driver: Driver; start: Date }>({
  /** App time when the test begins (the desk user signs in then). */
  start: [seconds(T0, 5), { option: true }],
  driver: async ({ page, start }, provide) => {
    const backend = await playwrightRequest.newContext({
      baseURL: BACKEND,
      userAgent: UA,
      extraHTTPHeaders: { Origin: ORIGIN },
    })
    const driver = new Driver(backend, page)
    await driver.reset(start)
    await page.clock.install({ time: driver.now })
    await signIn(page)
    await provide(driver)
    await backend.dispose()
  },
})

export { expect }
