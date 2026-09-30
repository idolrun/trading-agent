# Laya (laya-mlx) evaluation on the same 130 messages

_2026-09-30. Checkpoint `aac6fef/laya-mlx` (English, FP16), run on an Apple M4 with the exact questions Jev gets (`app/interpret/jev/questions.py`), a compact 512-token-friendly state, and Laya's answers converted with confidence = top probability. Scored against the same hand-made answer key as the Jev report (`jev-performance-2026-09-30.md`)._

## Verdict

Laya is not usable as a decision-maker for this task, as a fallback or as a second opinion. It is right about a quarter of the time on message type, its confidence carries no information, and it extracts almost none of the contract fields. Wiring it into the pipeline would add latency and memory for essentially no extra correct decisions, so it was **not** integrated.

## Message type (130 messages, argmax answer)

| | Laya | Jev (same messages) |
|---|---:|---:|
| Right at group level | 27% | 64% |
| Right at exact type | 12% | 57% |
| Trade-relevant detection: precision / recall | 41% / 63% | 80% / 78% |
| Entries found (of 21) | 8 | 19 |
| Exits found (of 22) | 5 | 13 |

- Laya and Jev name the same exact type on only 15 of 130 messages. Where they disagree, Laya is right at group level 21% of the time, Jev 63%.
- A plain `MSFT 520 0dte calls 1.15` came back as `correction` (62%). Only 7 of 24 clear messages got the right exact type with the JSON state, 3 of 24 with a plain-text state.

## Confidence does not mean anything

| Laya confidence | Messages | Right (group) |
|---|---:|---:|
| 0.0-0.5 | 49 | 33% |
| 0.5-0.7 | 35 | 20% |
| 0.7-0.9 | 22 | 27% |
| 0.9-1.0 | 24 | 25% |

The 0.9-1.0 band is right 25% of the time (Jev: 81%). No confidence threshold separates good answers from bad ones. Laya's authors document the same weakness (uncalibrated confidence, a fitted temperature that had to be clamped).

## Contract fields on the 17 single-contract entries

| Field | Laya | Jev |
|---|---|---|
| Strike | {'missed': 17} | {'right': 14, 'missed': 3} |
| Price | {'missed': 14, 'wrong': 2, 'right (not stated)': 1} | {'right': 16, 'right (not stated)': 1} |
| Call/put | {'right': 12, 'missed': 5} | {'right': 17} |
| Ticker | {'right (not stated)': 2, 'wrong': 6, 'invented': 4, 'right': 5} | {'right (not stated)': 5, 'right': 11, 'invented': 1} |
| Every contract field right | 0 of 17 | 12 of 17 |

## As a fallback after Jev (the plan's design)

Jev alone, with the current mapper: 37 messages decided, 93 left for a fallback. Laya on those 93, with the Jev veto (Laya may decide only if its type matches Jev's):

| Laya gate (t_chat/t_field/t_link) | Extra decisions | Right | Wrong |
|---|---:|---:|---:|
| strict .95/.9/.9 | 0 | 0 | 0 |
| default .9/.8/.8 | 1 | 1 | 0 |
| loose .8/.7/.7 | 1 | 1 | 0 |
| looser .7/.6/.6 | 1 | 1 | 0 |

Without the veto, the looser gates add a false action (a market comment read as a correction). Best case: one extra correct decision out of 93.

## Cost

- Latency per message (about 20 questions): median 4.1 s, p95 6.7 s, max 13.6 s. The README's 13 ms is for one short question. Jev's median is 0.38 s.
- About 3375 input tokens per message; the process showed about 17 GB resident in `top` during the run (README: about 1 GB).
- ~0.85 GB of weights downloaded from Hugging Face.
- Apple Silicon only.

## Things tried

- State as JSON with the question paths (main run), state as plain text, and the message alone: no improvement (plain text was worse).
- The `typed-decisions` checkpoint is not in the `aac6fef/laya-mlx` repo, so it was not tested. It is fine-tuned on four synthetic workflows, so it is unlikely to fit this task.
- Not tried: fine-tuning Laya on labelled messages. laya-mlx is inference only; training needs the upstream PyTorch code and a few thousand labelled examples (there are 130 hand-labelled ones).

## Limits

Same as the Jev report: hand-made labels, 130 messages, four providers, no open positions in the replay. The gap is large enough (27% vs 64%) that these limits do not change the conclusion.
