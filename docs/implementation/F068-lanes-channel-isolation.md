# F068 — Lanes: channel + provider isolation

## Objective
Make the **lane = (channel_id, provider_id)** the context, book and sequencing boundary (PRD C104, which supersedes C96 "per author").

## Before (audit)
- Every context read was author-wide across all channels: `recent_by_author`, `open_book`, `lock_open_book`, linking, C62 same-contract adds and the dispatcher slot.
- A channel-B "trim half" could link to a channel-A position.
- @mentioned authors' messages from any channel entered the model context, and the LLM could copy field values from them.

## What changed
| Area | Change |
|---|---|
| Dispatcher | Workers, wakeups and slots keyed by lane. `inbox.next_for_lane`, `lanes_waiting`, `later_applied(lane, …)`. NOTIFY payload `"<channel>:<provider>"`. |
| Context | `context_repo.recent_by_author(lane, …)` and `open_book(lane)` filter on the channel. `ProviderContext.mentioned_messages` removed (Jev state, prompts). |
| LLM citations | `llm_extract/schema.citable_texts`: a `context` field may only copy from the lane author's own messages. Another author's reply parent stays visible (read-only). |
| Ledger | `ledger.repo.lock_open_book(lane)`, `unlinked_follow_ups_after(lane, …)`. Index `(provider_id, opened_channel_id, status)`. |
| Relink | `RelinkService.recheck_after_late(lane, …)`. `apply_approved(position_id, signal_id)` derives the lane from the signal's event; a position outside it fails. |
| Review card | `ApprovalService._edit`: the chosen position must be one of the card's candidates and in the card's lane. |
| Manual ops | `POST /manual/positions` requires `channelId`. `link` refuses a position of another channel. `PositionOut.openedChannelId` added; the frontend add form uses the template position's lane. |
| Migration `0020` | Backfills `provider_positions.opened_channel_id` from the opening signal's message. A row still NULL belongs to no lane; the migration prints the count. |

## Same-contract rule (C62)
- Same provider + same channel + same contract → ADD (unchanged).
- Same provider + different channel + same contract → a new position.

There is no cross-channel link rule. Discord replies cannot cross channels, and threads map to their parent.

## Tests
- `tests/integration/graph/test_lanes.py`:
  - A channel-B trim modifies channel B's QQQ, not channel A's SPY.
  - A 0.99 hint at channel A's position from a channel-B message → AMBIGUOUS review card with lane-only candidates.
  - Another author in the same channel → NONE.
  - The same contract in two channels → two positions; again in channel A → LINKED.
- `tests/integration/dispatcher/test_sequencing.py`: one author in two channels runs as two parallel lanes; same-lane order is kept.
- `tests/integration/interpret/test_load_context.py`: context per channel; mentions bring no other author.
- `tests/integration/ledger/test_position_repo.py`: book per lane; a NULL channel is in no book.
