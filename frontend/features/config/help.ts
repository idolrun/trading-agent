/**
 * Help text for every config control, keyed by the control's `name` (the config key; nested
 * keys use `jev_*`). Each entry was written from the backend code that reads the
 * value (`backend/app/...`); nothing here is a guess. Order of fields in the popover is fixed.
 */
export type Help = {
  /** What the value is, in plain language. */
  means: string
  /** Which system behaviour changes when it changes. */
  affects: string
  example: string
  /** Accepted values, range or format. */
  constraints?: string
  /** Too high / too low / misconfigured. */
  risk?: string
  important?: string
}

const PCT = "Greater than 0 and at most 100 (percent, not a ratio)."
const POS_INT = "Whole number greater than 0."
const PROB = "Decimal from 0 to 1."

export const HELP: Record<string, Help> = {
  trading_mode: {
    means: "How approved orders are handled: shadow, paper or live.",
    affects:
      "shadow: orders go to an in-memory simulated broker only (nothing reaches a real broker, and broker reconciliation is skipped). paper: orders go to the Alpaca paper account. live: orders go to the real Alpaca account.",
    example: "paper = approved cards place orders in the Alpaca paper account.",
    constraints:
      "The server refuses to save a mode its running broker adapter cannot serve (paper needs the paper broker, live needs the live broker).",
    risk: "live places real orders with real money. Shadow and paper are not risk-free for decisions: they still exercise the same cards and risk checks. You type every quantity.",
    important: "Changing the mode takes effect on save for every order placed afterwards. A change summary is shown before saving.",
  },
  channels: {
    means: "The Discord channel ids the agent listens to.",
    affects:
      "A message from a channel not in this list is stored and shown in the feed as 'channel not configured' but is never interpreted, so it can never create a card.",
    example: "123456789012345678 (a Discord channel id, not the channel name).",
    constraints: "Numeric Discord ids (snowflakes) greater than 0, added one at a time. Duplicates are ignored.",
    risk: "An empty list means every message is skipped. A wrong id silently watches nothing.",
    important: "Enable Discord developer mode and copy the id; names are not accepted.",
  },
  muted_authors: {
    means: "Discord user ids whose messages are ignored.",
    affects: "A message whose author id is listed is stored and shown in the feed as 'muted author' but never interpreted. Channel filtering is checked first.",
    example: "987654321098765432 mutes that author in every watched channel.",
    constraints: "Numeric Discord ids (snowflakes) greater than 0, added one at a time.",
    risk: "Muting a provider you follow hides their signals and exits from the agent.",
  },
  feed_silence_s: {
    means: "How long the feed may go without a message or heartbeat before it counts as silent.",
    affects:
      "Checked every second against the newest of the last message and last heartbeat. During regular market hours a longer gap marks the feed degraded; outside market hours nothing changes. The flag clears when a signal arrives again.",
    example: "600 = 10 minutes.",
    constraints: `${POS_INT} Unit: seconds. Unset keeps the check off, raises an alert, and blocks entries.`,
    risk: "Too low: normal quiet periods raise false alarms. Too high: a dead connection goes unnoticed for longer.",
    important: "This only flags the problem. It does not recover missing messages.",
  },
  stale_entry_s: {
    means: "The oldest an entry or add message may be when processing starts.",
    affects:
      "Age is measured from the Discord post time to processing start. An older entry or add records no approvable card (the position ledger is still updated). Other stale messages (exits, stops) still produce cards, marked 'late'. Also used by catch-up detection.",
    example: "30 = an entry processed 31 s after it was posted yields no entry card.",
    constraints: `${POS_INT} Unit: seconds. Default 30.`,
    risk: "Too low: processing delays reject valid entries. Too high: you may act on an old price.",
    important: "Does not block follow-up exits or stop messages.",
  },
  catch_up_backlog_n: {
    means: "How many unprocessed messages may wait before the system reports it is catching up.",
    affects:
      "Checked every second: catch-up is on when more than this many events are waiting OR the oldest waiting event is older than 'Stale entry'. It clears when neither holds ('backlog drained'). It sets a system flag shown in health; it is not a queue size and does not change how many messages are processed at once.",
    example: "20 = catch-up turns on when 21 or more messages wait.",
    constraints: `${POS_INT} Unit: messages. Unset keeps the check off, raises an alert, and blocks entries.`,
    risk: "Too low: brief bursts show catch-up. Too high: a real backlog is reported late.",
  },
  poison_retry_limit: {
    means: "How many processing attempts a message gets before it counts as repeatedly failing.",
    affects:
      "When attempt number reaches this limit the message is marked failed, an alert is raised and a raw review card is created so a person handles it. Earlier failures retry the same message. Connection outages never count towards the limit.",
    example: "3 = the third failed attempt sends the message to review.",
    constraints: `${POS_INT} Unit: attempts. Default 3.`,
    risk: "Too low: temporary faults go to manual review. Too high: a permanently failing message is retried longer before anyone sees it.",
  },
  context_recent_messages: {
    means: "How many earlier messages are shown to the interpreter alongside the current one.",
    affects:
      "Used by live processing, replay and labeling alike. It takes up to this many earlier messages by the same author in the same lane (never other authors), as of the current message's post time, plus the same number of the lane's recent signal events. The reply parent is added separately.",
    example: "5 = up to 5 earlier messages from that author.",
    constraints: `${POS_INT} Unit: messages. Default 5.`,
    risk: "More context helps resolve follow-ups like 'add more' but adds model input and time. Less context can leave follow-ups unlinked.",
  },
  entry_chase_pct: {
    means: "How far above the signal price an entry may be placed.",
    affects:
      "Entry limit = signal price x (1 + percent / 100), rounded down to the tick. At approval the card is blocked if the live ask is already above that price band.",
    example: "5 on a $1.00 signal = limit $1.05.",
    constraints: `${PCT} Unset blocks entries.`,
    risk: "Too high: you may pay well above the signal. Too low: cards expire out of band as the price moves.",
    important: "Separate from 'Identity price tolerance', which compares the signal price with the quote.",
  },
  entry_validity_s: {
    means: "How long an entry or add card can be approved.",
    affects: "Card expires at post time + this many seconds (measured from the Discord post, not from card creation). An expired card cannot be approved. Exit cards use 'Exit card timeout'.",
    example: "60 = approve within 1 minute of the post.",
    constraints: `${POS_INT} Unit: seconds. Unset blocks entries.`,
    risk: "Too low: you may not be able to approve in time. Too high: you can approve entries on old signals.",
  },
  entry_cancel_after_s: {
    means: "How long an unfilled entry order may rest at the broker.",
    affects: "After this many seconds the unfilled remainder is cancelled. Partial fills stay in the position.",
    example: "30 = the unfilled rest is cancelled 30 s after the order is placed.",
    constraints: `${POS_INT} Unit: seconds. Unset blocks entries.`,
    risk: "Too low: orders are cancelled before they can fill. Too high: stale limit orders stay live.",
  },
  quote_max_age_s: {
    means: "The oldest market quote the system will act on.",
    affects:
      "A quote older than this is never used: validation sends the signal to review ('quote stale') and approval re-checks block. Age uses the data source's own timestamp, so a delayed feed counts as delayed.",
    example: "1200 = 20 minutes (needed for Alpaca's ~15 min delayed free options data).",
    constraints: `${POS_INT} Unit: seconds. Unset blocks entries.`,
    risk: "Too low for your data feed: everything is blocked as stale. Too high: decisions use old prices.",
  },
  exit_card_timeout_s: {
    means: "How long an exit-type card can be approved.",
    affects: "Exit cards expire at post time + this many seconds. Exits are never blocked by price.",
    example: "300 = approve within 5 minutes of the post.",
    constraints: `${POS_INT} Unit: seconds. Unset blocks entries.`,
    risk: "Too low: you may miss the chance to exit. Too high: old exit cards stay approvable.",
  },
  exit_reprice_interval_s: {
    means: "Time between price adjustments of an unfilled exit order.",
    affects: "Every interval the unfilled rest is cancelled and re-placed one step lower toward the bid. If this or the step is unset, the exit rests at the mid price.",
    example: "5 = adjust every 5 seconds.",
    constraints: `${POS_INT} Unit: seconds. Unset blocks entries.`,
    risk: "Too low: churns orders. Too high: slow to get out.",
  },
  exit_reprice_step_pct: {
    means: "How far each exit re-price moves toward the bid.",
    affects: "Each step lowers the price by this percent of the mid-bid spread (at least one tick), never below the floor.",
    example: "25 = a quarter of the spread per step.",
    constraints: `${PCT} Unset blocks entries.`,
    risk: "Too high: gives up the spread quickly. Too low: slow to fill.",
  },
  exit_floor_pct: {
    means: "The lowest exit price, as a percent below the mid price when the exit starts.",
    affects: "Floor = start mid x (1 - percent / 100), never below one tick. If the exit is still unfilled at the floor it stops, raises an urgent alert and restores protection.",
    example: "10 on a $2.00 mid = floor $1.80.",
    constraints: `${PCT} Unset blocks entries.`,
    risk: "Too high: may exit at a poor price. Too low: the exit may stop unfilled and need manual action.",
  },
  cutoff_1_min_before_close: {
    means: "Minutes before the session close when an expiring option position gets an urgent close card.",
    affects: "For option lots expiring today, an urgent 'close' card for the whole lot is created at close minus this many minutes and approved like any card. Uses the real session close, so half days work.",
    example: "30 = at 3:30 pm ET on a normal day.",
    constraints: `${POS_INT} Unit: minutes. Must be greater than Cutoff 2. Unset blocks entries.`,
    risk: "Too low: little time to approve before the forced close.",
  },
  cutoff_2_min_before_close: {
    means: "Minutes before the session close when an expiring option position is closed automatically.",
    affects: "For option lots expiring today, a forced close is sent without approval unless an approved exit is already working, with an urgent alert.",
    example: "10 = forced close at 3:50 pm ET on a normal day.",
    constraints: `${POS_INT} Unit: minutes. Must be smaller than Cutoff 1. Unset blocks entries.`,
    risk: "Too high: closes earlier than needed. Too low: the order may not fill before close.",
    important: "This is an automatic order, not a card.",
  },
  max_daily_loss_usd: {
    means: "The most you accept to lose in one trading day before entries stop.",
    affects: "New entry and add cards are disabled ('max daily loss reached') once realized loss today plus unrealized loss reaches this amount. Exits and stops are never blocked.",
    example: "500 = entries stop at a $500 loss.",
    constraints: "Dollar amount greater than 0. Unset blocks entries.",
    risk: "Too high: weaker protection. Too low: blocks entries after a small loss.",
    important: "If a quote or cost basis is missing, risk is unknown and entries are blocked too.",
  },
  max_open_positions: {
    means: "The most positions that may be open at once.",
    affects: "A new entry card is disabled ('max open positions') when the open count has reached the limit. Adds to an existing position do not count. The count includes broker holdings and unfilled entry orders the ledger does not yet show.",
    example: "5 = a sixth entry is blocked.",
    constraints: `${POS_INT} Unit: positions. Unset blocks entries.`,
    risk: "Too high: weaker diversification limit. Too low: blocks valid entries.",
  },
  max_total_exposure_usd: {
    means: "The most money that may be committed across positions and working entry orders.",
    affects:
      "An entry or add is blocked ('max exposure') when current exposure plus this order's cost (quantity x limit x 100 for options) would exceed it. Exposure = cost basis of open lots (including foreign ones) plus unfilled entry orders, and the larger of ledger and broker cost where the broker holds more.",
    example: "20000 = $20,000.",
    constraints: "Dollar amount greater than 0. Unset blocks entries.",
    risk: "Too high: permits larger exposure. Too low: blocks otherwise valid entries.",
    important: "Not account equity or buying power; buying power is checked separately.",
  },
  identity_price_tolerance_pct: {
    means: "How close the signal's option price must be to the live quote.",
    affects:
      "The signal price (read as per share or per contract, whichever is closest) must be within this percent of the live mid. If not, the signal goes to review as 'price off quote'. Not applied to stocks.",
    example: "15 = within 15% of the mid.",
    constraints: `${PCT} Unset sends option signals with a price to review.`,
    risk: "Too strict: valid signals need review. Too wide: a wrong contract can pass this check.",
    important: "Separate from the entry chase limit.",
  },
  adjacent_strike_ratio: {
    means: "How much closer a neighbouring strike must be to the signal price to trigger a warning.",
    affects:
      "A warning ('adjacent strike better match') is added when the next strike up or down has a mid price whose distance to the signal price is at most ratio x the named strike's distance, and strictly smaller. It is a warning only, never a block.",
    example: "0.5 = a neighbour must be at least twice as close to the signal price.",
    constraints: "Decimal greater than 0. Unset turns the check off.",
    risk: "Higher: more warnings. Lower: fewer warnings, possible typo in the strike goes unflagged.",
  },
  symbol_map: {
    means: "Rewrites a ticker from a message into the symbol your broker uses.",
    affects: "Applied to the underlying of every signal (case-insensitive) before contract lookup.",
    example: "SPX to SPXW.",
    constraints: "Pairs of text symbols. Rows with an empty name are dropped.",
    risk: "A wrong mapping looks up the wrong contract.",
  },
  missing_underlying_proximity_pct: {
    means: "How near a ticker's price must be to the strike to be suggested when the message names no ticker.",
    affects: "Among tickers this provider traded recently, one is suggested when |strike - price| / price x 100 is at most this percent. Zero or several matches suggest nothing.",
    example: "10 and a 550 strike: a ticker priced 500 to 611 matches.",
    constraints: `${PCT} Default 10.`,
    risk: "Too wide: several tickers match, so none is suggested. Too narrow: the right one is missed.",
    important: "A suggestion is never confirmed for you: the card needs explicit underlying confirmation.",
  },
  missing_underlying_lookback_days: {
    means: "How far back to look for tickers the provider traded.",
    affects: "Tickers the provider traded since now minus this many days (a plain day count, not trading days) are candidates. Unset means no suggestions.",
    example: "7 = the last seven days.",
    constraints: `${POS_INT} Unit: days. Unset blocks entries.`,
    risk: "Too long: more candidates, more ambiguity. Too short: misses the right ticker.",
  },
  t_chat: {
    means: "Confidence needed to treat a message as plain chat.",
    affects: "A chat-like message is kept as feed-only only if the model's type confidence is at least this; otherwise it goes to the slower review path.",
    example: "0.9 = 90% confident.",
    constraints: PROB,
    risk: "Too low: real signals may be filed as chat. Too high: more chat goes to the slow path.",
  },
  t_field: {
    means: "Confidence needed for each extracted field (ticker, strike, price).",
    affects: "A field below this confidence adds a 'low confidence' review reason to the card.",
    example: "0.8 = 80%.",
    constraints: PROB,
    risk: "Too low: shaky fields pass unflagged. Too high: more cards flagged for review.",
  },
  t_type: {
    means: "Confidence needed to act on the message type (entry, exit, trim and so on).",
    affects: "Below this the message goes to the slower path instead of being assembled directly.",
    example: "0.8 = 80%.",
    constraints: PROB,
    risk: "Too low: misread types can act. Too high: more messages take the slow path.",
  },
  t_link: {
    means: "Confidence needed when tying a message to an existing position.",
    affects: "Used when the interpreter decides which open position a follow-up refers to.",
    example: "0.8 = 80%.",
    constraints: PROB,
    risk: "Too low: a follow-up may attach to the wrong position. Too high: more links need review.",
  },
  jev_model: {
    means: "The Jev model name used as the primary interpreter.",
    affects: "Every message is first sent to this model. Thresholds were measured on the pinned version.",
    example: "jev-1.13.0",
    constraints: "Text; blank uses no explicit model. Aliases like jev-latest can change behaviour without notice.",
    risk: "Changing the version changes interpretation. Re-run a replay before switching.",
  },
  jev_timeout_ms: {
    means: "How long one Jev request may take.",
    affects: "A request slower than this counts as a model failure and the message falls to the fallbacks.",
    example: "700 = 0.7 seconds.",
    constraints: `${POS_INT} Unit: milliseconds. Queue wait + this must fit in the step budget.`,
    risk: "Too low: frequent fallbacks. Too high: slower handling of every message.",
  },
  jev_queue_timeout_ms: {
    means: "How long to wait for a free request slot.",
    affects: "If no slot frees up in time, the Jev step fails and falls to the fallbacks.",
    example: "250 = a quarter of a second.",
    constraints: `${POS_INT} Unit: milliseconds.`,
    risk: "Too low: fails during bursts. Too high: delays messages.",
  },
  jev_budget_ms: {
    means: "The total time allowed for the whole Jev step.",
    affects: "Caps queue wait plus all attempts. A rate-limit retry happens only if a full request still fits.",
    example: "1200 = 1.2 seconds.",
    constraints: `${POS_INT} Unit: milliseconds. Must be at least queue wait + request timeout.`,
    risk: "Too low: no retries. Too high: slower fallback.",
  },
  jev_enabled: {
    means: "Turns the primary interpreter on or off.",
    affects: "Off, or without the TypeSafe API key, every message goes to the LLM (when configured), else review.",
    example: "On = Jev reads each message first.",
    risk: "Off sends all traffic to the slower LLM and review.",
  },
  llm: {
    means: "The optional language-model registry: which models handle which job, in what order.",
    affects:
      "roles.extract / note / prelabel each have deadline_ms, per_attempt_cap_ms and a fallback chain of {provider, model, prompt, gate_run}. An empty chain means that role is not configured. prices (USD per million tokens), params and concurrency (max parallel calls per provider, default 4) are keyed by 'provider:model'.",
    example: '{"roles": {"note": {"deadline_ms": 4000, "chain": []}}}',
    constraints: "Valid JSON. The server checks everything else (providers anthropic, openai, deepseek, openai_compat; chain entries need a passing evaluation run) and rejects a bad document.",
    risk: "A wrong chain can send message text to the wrong model or leave a role unconfigured.",
  },
  note_guard_enabled: {
    means: "Checks the generated card note before showing it.",
    affects: "On: a note containing values not backed by the message or card is hidden. Off: every generated note is shown unchecked.",
    example: "On = a note quoting a price the signal never mentioned is hidden.",
    risk: "Off can show a made-up number on a card you act on.",
  },
  llm_monthly_budget_usd: {
    means: "Monthly spending limit for language-model calls.",
    affects: "A warning alert at 80% of the month's spend and an urgent alert at 100%. Calls are not shut off.",
    example: "100 = alerts at $80 and $100.",
    constraints: "Dollar amount greater than 0. Unset blocks entries.",
    risk: "Too low: constant alerts. Too high: overspending goes unnoticed.",
  },
}

/** Shown for a field when the config key is `HELP` keyed differently (never expected). */
export const helpFor = (name: string): Help | undefined => HELP[name]
