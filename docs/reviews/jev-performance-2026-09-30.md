# How Jev performs on the first 130 signal messages

> ## Update: after the mapper change (same day)
>
> `backend/app/interpret/jev/mapping.py` no longer sends a message to the LLM fallback because of answers to questions that do not apply to it. A stop-related yes/no flag or `stop_basis` now blocks only when the message has a stop; `exit_fraction` only when it is an exit; `is_add` never blocks; `is_correction` only when Jev leans yes. Msg-type, ticker, strike, right, price, size-tier and condition checks are unchanged, and the app's default thresholds are unchanged.
>
> | Full 518-message replay | Before | After |
> |---|---:|---:|
> | Feed-only | 136 | 139 |
> | Assembled by Jev into a trade signal | 0 | 19 |
> | Sent to the LLM fallback (raw review card, "unparsed") | 382 | 360 |
>
> - **The 19:** 17 entries, 1 trim, 1 out-of-scope. All 17 entries are real entries with the right ticker, strike, call/put, expiry and price; stop and take-profit levels were extracted correctly where present (e.g. `MSTR 10/2 150 PUTS 2.45 SL 1.90 TP 2.90 / 3.3 / 3.8`). 14 became entry cards that stop at contract validation (the fake market data has no chains); 2 (`$SPY 765P @ 1.42`, `qqq 737 puts 1.36`) correctly stopped for a missing expiry.
> - **Two mistakes among the 19:** `BE calls now 25%` (a P&L note) became a trim and stopped at the link check with no position to attach to; `selling some BE calls here at 2.55` (a partial sale) was called out-of-scope, so no exit card.
> - **On messages 1-130** the default thresholds now decide 36 messages (4 trade signals, all correct, no false actions); 46 of the 50 actionable messages still go to the LLM or a person.
> - **Still open:** the LLM fallback is not configured, so about 70% of messages, including most exits and stop updates, are still unparsed review cards. The tables below are the "before" analysis and use the old mapper.


_Replayed 2026-09-30 through the real pipeline (Jev model `jev-latest`, own database, clock set to each message's original time). Scored against a hand-made answer key. Messages 1-130 of the export, 129 scored (one Jev call timed out)._

## Summary

- **Jev reads the messages reasonably well on its own.** Its top answer for message type matches the answer key 65% of the time at group level (59% at exact type). It finds trade-relevant messages (entries, exits, stop/target changes) with 83% precision and 78% recall.
- **Its confidence is a usable signal.** When Jev is at least 90% sure of the message type it is right 88% of the time. Below 70% it is right about half the time, no better than a coin flip.
- **Contract fields are strong when it commits.** Call/put and price were right on every entry it found; the ticker was right (or correctly left out) on 15/16 and the strike on 13/16 (the misses are listed below). 11 of the 16 single-contract entries came out fully correct.
- **With the app's default thresholds Jev is never allowed to decide a trade.** 32 messages were accepted as feed-only, **0 were assembled into a trade signal**, and 98 were sent to the LLM fallback. That fallback is not configured, so all of them became raw review cards ("unparsed").
- **Letting Jev decide with a sane policy works on this sample.** Keep a confidence gate on the message type but stop blocking on the noisy yes/no flags: at 0.8 Jev makes 48 decisions (8 trade signals), none is a false entry, none has a wrong contract, but 2 exits/stops are called chatter; at 0.6 it makes 61 decisions with the same record (3 missed). It still cannot cover most actionable messages (9 of 50); the rest need the LLM or a human.
- **Main weaknesses:** implicit exits ("gotta go here", "Stopped last 3", "pop it here") read as chatter; price-only updates ("1350/con", "7740c 1100/con") read as entries, though only at low confidence; stop levels ("743.50 is cut") read as exits.

## 1. Message type

Groups: entry (entry/add/stock entry), exit (trim/full exit/soft exit), manage (stop set/move, take-profit set), notice (P&L and price updates), view (watch/conditional), chatter.

| Group | In answer key | Jev said | Jev right | Recall | Precision |
|---|---:|---:|---:|---:|---:|
| entry | 20 | 24 | 18 | 90% | 75% |
| exit | 22 | 14 | 13 | 59% | 93% |
| manage | 8 | 9 | 5 | 62% | 56% |
| notice | 22 | 9 | 4 | 18% | 44% |
| view | 20 | 8 | 8 | 40% | 100% |
| chatter | 37 | 65 | 36 | 97% | 55% |

Rows = answer key, columns = Jev:

| | entry | exit | manage | notice | view | chatter |
|---|---:|---:|---:|---:|---:|---:|
| **entry** | 18 | 0 | 0 | 0 | 0 | 2 |
| **exit** | 1 | 13 | 1 | 3 | 0 | 4 |
| **manage** | 0 | 1 | 5 | 1 | 0 | 1 |
| **notice** | 4 | 0 | 1 | 4 | 0 | 13 |
| **view** | 1 | 0 | 2 | 0 | 8 | 9 |
| **chatter** | 0 | 0 | 0 | 1 | 0 | 36 |

## 2. Confidence vs accuracy

| Jev confidence | Messages | Right (group) | Right (exact type) |
|---|---:|---:|---:|
| 0.0-0.5 | 36 | 53% | 42% |
| 0.5-0.7 | 27 | 52% | 41% |
| 0.7-0.9 | 26 | 62% | 58% |
| 0.9-1.0 | 40 | 88% | 88% |

The high-confidence band is reliable; everything else is noisy, so a gate on confidence is worth keeping.

## 3. Entry fields (20 single-contract entries in the key, 16 with a Jev answer)

| Field | Right | Right (correctly 'not stated') | Missed | Invented |
|---|---:|---:|---:|---:|
| underlying | 11 | 4 | 0 | 1 |
| strike | 13 | 0 | 3 | 0 |
| right | 16 | 0 | 0 | 0 |
| expiry | 7 | 8 | 0 | 1 |
| price | 15 | 1 | 0 | 0 |

Misses and inventions:
- message 16: strike missed (key 1125, Jev None)
- message 17: strike missed (key 7750, Jev None)
- message 30: strike missed (key 377.5, Jev None)
- message 35: expiry invented (key None, Jev 'friday')
- message 111: underlying invented (key None, Jev 'MOC')

('friday' on message 35 is arguably correct, since it was posted on a Friday; the key marks the expiry as not stated.)

## 4. Who decides: threshold policies (before the mapper change)

Each row re-runs the app's own mapper and router on Jev's stored answers with different thresholds. 'Decided' = accepted without the LLM or a human. Errors that matter are separated from harmless mix-ups (e.g. chatter vs a P&L note).

| Policy | Decided | Trade signals | Right | Harmless mix-up | Missed action | False action | Wrong action / contract | To LLM or human |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| app default | 32 | 0 | 27 | 4 | 1 | 0 | 0 | 97 |
| relaxed A | 40 | 0 | 30 | 8 | 2 | 0 | 0 | 89 |
| relaxed B | 47 | 1 | 33 | 11 | 3 | 0 | 0 | 82 |
| relaxed C | 56 | 5 | 39 | 14 | 3 | 0 | 0 | 73 |
| relaxed D | 79 | 22 | 53 | 18 | 4 | 1 | 3 | 50 |
| no gate | 99 | 26 | 61 | 23 | 11 | 1 | 3 | 30 |
| hybrid >=0.9 | 37 | 5 | 32 | 4 | 1 | 0 | 0 | 92 |
| hybrid >=0.8 | 48 | 8 | 38 | 8 | 2 | 0 | 0 | 81 |
| hybrid >=0.7 | 56 | 10 | 42 | 11 | 3 | 0 | 0 | 73 |
| hybrid >=0.6 | 61 | 10 | 44 | 14 | 3 | 0 | 0 | 68 |

- *Missed action*: an exit/stop/entry message Jev called chatter or a notice. *False action*: a non-trade message Jev turned into a trade signal.
- 'app default' is what ran. 'relaxed A-D' lower every threshold together. 'hybrid' keeps a minimum on the message-type confidence but ignores the low-confidence yes/no flags and 'not applicable' answers that block the default.
- The unfiltered policies ('relaxed D', 'no gate') do produce false actions and wrong contracts, so lowering everything is not safe.

Errors under the recommended-looking policies:
- **hybrid >=0.8**: msg 50 (soft_exit) called chatter; msg 53 (soft_exit) called chatter
- **hybrid >=0.6**: msg 50 (soft_exit) called chatter; msg 53 (soft_exit) called chatter; msg 78 (full_exit) called chatter

## 5. Notable Jev mistakes (group level, trade-relevant)

| # | Message | Answer key | Jev | Conf |
|---:|---|---|---|---:|
| 17 | Im in 7750cs at 250 but it already ran @here fudge | entry | chatter | 0.39 |
| 20 | bought that dip into support @everyone lets see how she shak | add | chatter | 0.40 |
| 27 | back at entry | commentary_pnl | entry | 0.44 |
| 32 | 1st tp hit sold 1/4  @here | trim | tp_hit_notice | 0.88 |
| 33 | stopped quick | full_exit | chatter | 0.34 |
| 34 | need to hold 375.50 for me to have any interest a | watch | stop_set | 0.58 |
| 46 | stop breakeven on runners, but think we get in the money @ev | stop_move | commentary_pnl | 0.45 |
| 49 | HOD 518 would be ultimate target fyi if she gets froggy | watch | tp_set | 0.66 |
| 50 | gotta go here | soft_exit | chatter | 0.95 |
| 53 | pop it here @everyone | soft_exit | chatter | 0.83 |
| 60 | Holy chop fest 7740c at 290/con @here size for 0 | entry | timeout | - |
| 63 | 743.50 is cut | stop_set | full_exit | 0.37 |
| 67 | 500/con @here manage yourselves | commentary_pnl | entry | 0.39 |
| 78 | Stopped last 3 have a good weekend @everyone | full_exit | chatter | 0.71 |
| 79 | 7740c 1100/con @here | commentary_pnl | entry | 0.52 |
| 81 | 1350/con @here | commentary_pnl | entry | 0.69 |
| 84 | more 60% @everyone nice reclaim of yest high | trim | commentary_pnl | 0.50 |
| 90 | Runners .63 @everyone | trim | entry | 0.18 |
| 91 | **32%** more @everyone | trim | commentary_pnl | 0.40 |
| 92 | Recent high or breakeven on rest @everyone | stop_move | chatter | 0.22 |
| 101 | 180 -> 300 @here | commentary_pnl | tp_set | 0.32 |
| 114 | cut NOW swing @everyone not swinging | full_exit | stop_set | 0.36 |
| 120 | DJT — Call swings end of OCT DKNG — Call swings end of OCT F | watch | entry | 0.56 |

## 6. Speed and availability

- Calls: 129 ok, 1 timeout (10 s limit).
- Latency: median 383 ms, p95 688 ms, max 7849 ms. The design target is 300 ms p95, which this setup does not meet; the dev config uses a 10 s timeout for that reason.

## 7. What to do with this

1. **Use Jev as the first decision-maker, gated on message-type confidence (>= 0.8), and stop gating on `is_add`, `stop_basis`, `size_tier` and the other yes/no flags.** On this sample that gave no false entries and no wrong contracts. It needs a small code change in `backend/app/interpret/jev/mapping.py`; threshold settings alone cannot do it.
2. **Keep the ticker/strike/right checks.** They caught the incomplete contracts (index options without a ticker, `MOC` read as a ticker).
3. **Configure the LLM fallback** (two providers) for the messages Jev is unsure about, especially exits: 9 exit and stop messages were read as chatter or as P&L notices.
4. **Treat price-only messages as unsafe to auto-enter.** Jev called `7740c 1100/con` an entry at 52%; the confidence gate stops this, so do not lower it below 0.6.
5. Re-run on a larger, independently labelled set before trusting any threshold with real money.

## Method and limits

- The answer key was written by reading each message against the app's message-type definitions; ambiguous ones (e.g. `1200 @here I paid you get out`) are judgement calls.
- No positions existed in the replay, so follow-up linking (which trade a trim or exit belongs to) was not measured. Reply links were passed as Discord reply ids.
- The market data is a fake with no option chains, so contract validation and fills are out of scope.
- 130 messages from four providers over about a day and a half; 20 entries. Percentages on the small groups move by several points per message.
