"""Labeling API (F048, PRD §17). One `Label` shape everywhere: the API, the model's structured
output, and (snake_case, `by_alias=False`) `eval_labels.label`.

    {"signals": [{"idx": 0, "msg_type": "entry", "instrument": "option",
                  "fields": {"underlying": "GOOGL", "strike": "357.5", "right": "call", ...}}],
     "link": {"idx": 0, "provider_position_id": "<uuid>"}}

`fields` holds plain expected values under the `SignalFields` names (no provenance); an empty
`signals` list is a labeled non-signal. `corrected` is F047's provider-correction form (edited
position fields, not scored). `link` may be absent (= null).

`eventKey` = `<message_id>.<event_type>.<event_seq>`, e.g. `1234567890.create.0`.
"""

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import ConfigDict, Field, field_validator

from app.api.schemas.base import CamelModel, DiscordId
from app.evalkit.models import LabelSource, Split
from app.interpret.models import MsgType
from app.interpret.signal import SignalFields


class _Strict(CamelModel):
    model_config = ConfigDict(extra="forbid")


class LabelSignal(_Strict):
    idx: int = Field(ge=0)
    msg_type: MsgType
    instrument: Literal["option", "stock", "unclear"] | None = None
    fields: dict[str, Any] = Field(
        default={}, description="Expected plain values by SignalFields name; omit what is unset."
    )
    corrected: dict[str, Any] | None = None

    @field_validator("fields")
    @classmethod
    def _known_fields(cls, v: dict[str, Any]) -> dict[str, Any]:
        if unknown := sorted(v.keys() - SignalFields.model_fields.keys()):
            raise ValueError(f"unknown signal fields: {', '.join(unknown)}")
        return v


class LabelLink(_Strict):
    idx: int = Field(ge=0)
    provider_position_id: uuid.UUID


class Label(_Strict):
    signals: list[LabelSignal]
    link: LabelLink | None = None


class ContextMessageOut(CamelModel):
    id: DiscordId
    at_et: str
    text: str
    is_reply_parent: bool = False


class LabelRecord(CamelModel):
    label_id: uuid.UUID
    label: Label
    source: LabelSource
    split: Split
    trading_day: date
    labeled_at: datetime
    supersedes_label_id: uuid.UUID | None


class QueueItem(CamelModel):
    event_key: str
    provider_id: DiscordId
    posted_at: datetime
    text: str
    context: list[ContextMessageOut]  # the provider's own earlier messages (C71 scope)
    prelabel: Label | None  # the LLM's pending suggestion (latest ok `prelabel` model call)


class LabelDetail(QueueItem):
    labels: list[LabelRecord]  # newest first: [0] is current, the rest were superseded


class LabelPut(CamelModel):
    label: Label
    supersedes: uuid.UUID | None = Field(
        default=None,
        description="The label this replaces; must be the current one (default: the current).",
    )


class LabelProgress(CamelModel):
    """Labeled events toward the PRD §17 replay-gate minimums (current labels only)."""

    actionable: int
    non_actionable: int
    providers: int
    min_actionable: int
    min_non_actionable: int
    min_providers: int
