"""F033: Appendix A mapping and TRD §4.4 routing, on recorded-shape Jev answers."""

import uuid
from decimal import Decimal

from typesafe_sdk import Choice

from app.interpret.candidates import find_candidates
from app.interpret.context import BookEntry
from app.interpret.jev.mapping import map_answers
from app.interpret.jev.questions import NUMBER_ROLES, build_questions
from app.interpret.jev.state import build_jev_state
from app.interpret.models import MsgType
from app.interpret.route import route
from app.interpret.signal import LiteralSpan, ModelSource, ResolvedSource
from tests.jev_helpers import CFG, EXAMPLE, c, example_answers, n, score
from tests.llm_helpers import ctx

P1 = uuid.UUID("00000000-0000-0000-0000-0000000000b1")


def run(text: str = EXAMPLE, book: list[tuple[uuid.UUID, str]] = (), **overrides):  # type: ignore[no-untyped-def,assignment]
    context = ctx(text, book=book)
    candidates = find_candidates(text)
    result = map_answers(example_answers(**overrides), context, candidates, CFG)
    return result, route(result, CFG)


def test_trd_example_assembles_literal_fields_from_candidates() -> None:
    _, decision = run()
    assert decision.path == "assemble" and not decision.link_ambiguous
    assert decision.parsed is not None and decision.parsed.interpreter == "jev"
    [signal] = decision.parsed.signals
    f = signal.fields
    assert signal.msg_type is MsgType.ENTRY and signal.instrument == "option"
    assert f.strike is not None and f.strike.value == Decimal(770)
    assert f.entry_price is not None and f.entry_price.value == Decimal("0.95")
    assert f.stop_level is not None and f.stop_level.value == Decimal(769)
    assert f.stop_basis == "underlying"
    assert f.underlying is not None and f.underlying.value == "spy"
    assert f.right is not None and f.right.value == "call"
    assert f.expiry_token is None
    by_candidate = {
        "strike": "n1",
        "entry_price": "n2",
        "stop_level": "n3",
        "underlying": "t1",
    }
    for name, cid in by_candidate.items():
        source = getattr(f, name).source
        assert isinstance(source, LiteralSpan) and source.candidate_id == cid
        assert EXAMPLE[source.start : source.end] == source.raw  # value came from the span
    assert signal.field_confidence["strike"] == Decimal("0.97")


def test_two_numbers_claiming_the_strike_go_to_the_slow_path() -> None:
    _, decision = run(role_n2=c("strike", 0.9))
    assert decision.path == "slow_path"
    assert any("claimed by n1, n2" in r for r in decision.reasons)
    assert decision.hints is not None and decision.hints["role_n2"]["choice"] == "strike"


def test_low_confidence_or_missing_required_field_goes_to_the_slow_path() -> None:
    assert run(role_n1=c("strike", 0.6))[1].path == "slow_path"
    assert run(ticker=c("not_stated"))[1].path == "slow_path"  # underlying required for entry
    assert run(msg_type=c("entry", 0.7))[1].path == "slow_path"
    assert run(signal_count=c("multiple"))[1].path == "slow_path"
    assert run(has_prose_condition=n(0.9))[1].path == "slow_path"  # the LLM reads the words
    assert run(candle_close=n(0.5))[1].path == "slow_path"  # yes/no uncertain


PLAIN = "spy 770c .95 lotto"  # an entry with no stop


def test_questions_that_do_not_apply_do_not_block_a_plain_entry() -> None:
    """Replay of 518 real messages: Jev answers these at 0.2-0.5 and nothing assembled."""
    _, decision = run(
        PLAIN,
        stop_basis=c("not_applicable", 0.27),
        exit_fraction=c("not_stated", 0.3),
        candle_close=n(0.5),
        stop_removed=n(0.4),
        breakeven_stop=n(0.5),
        is_correction=n(0.3),
    )
    assert decision.path == "assemble", decision.reasons
    assert decision.parsed is not None
    f = decision.parsed.signals[0].fields
    assert f.stop_basis is None and f.exit_fraction is None


def test_a_confident_flag_is_still_recorded_without_a_stop() -> None:
    _, decision = run(PLAIN, candle_close=n(0.95))
    assert decision.parsed is not None and decision.parsed.signals[0].fields.candle_close


def test_the_same_doubt_blocks_when_the_message_has_a_stop() -> None:
    for name, answer in (
        ("stop_removed", n(0.5)),
        ("breakeven_stop", n(0.5)),
        ("stop_basis", c("underlying", 0.5)),
    ):
        assert run(**{name: answer})[1].path == "slow_path", name  # EXAMPLE has "sl 769"
    assert (
        run("spy 770c .95 sl 769", stop_removed=n(0.5), role_n2=c("entry_price"))[1].path
        == "slow_path"
    )


def test_an_uncertain_exit_fraction_blocks_an_exit_but_not_an_entry() -> None:
    exit_answers = {
        "msg_type": c("trim"),
        "ticker": c("not_stated"),
        "right": c("not_stated"),
        "exit_fraction": c("half", 0.5),
    }
    assert run("sold half", **exit_answers)[1].path == "slow_path"
    exit_answers["exit_fraction"] = c("half", 0.95)
    assert run("sold half", **exit_answers)[1].path == "assemble"


def test_a_worded_condition_or_a_likely_correction_still_goes_to_the_llm() -> None:
    assert run(PLAIN, has_prose_condition=n(0.4))[1].path == "slow_path"  # a real chance of "if..."
    assert run(PLAIN, is_correction=n(0.6))[1].path == "slow_path"
    assert run(PLAIN, is_correction=n(0.3))[1].path == "assemble"


def decide(text: str, cfg=CFG, **kw):  # type: ignore[no-untyped-def]
    """map_answers + route with extra keyword args / a config copy (`run` takes neither)."""
    overrides = kw.pop("answers", {})
    result = map_answers(example_answers(**overrides), ctx(text), find_candidates(text), cfg, **kw)
    return route(result, cfg)


def test_an_unsure_number_that_is_surely_not_a_field_does_not_block() -> None:
    junk = "spy 770c .95 size for 0"  # the 0 is neither strike, price nor stop
    sure_not_a_field = c("other", 0.4, date_or_time=0.3, pnl_or_percent=0.2)
    assert decide(junk, answers={"role_n3": sure_not_a_field}).path == "assemble"
    could_be_the_strike = c("other", 0.4, strike=0.35)
    assert decide(junk, answers={"role_n3": could_be_the_strike}).path == "slow_path"


def test_missing_underlying_becomes_a_card_only_when_asked_and_only_for_options() -> None:
    no_ticker = {"ticker": c("not_stated")}
    assert decide(PLAIN, answers=no_ticker).path == "slow_path"  # an LLM may infer it (A.3)
    optional = decide(PLAIN, answers=no_ticker, underlying_optional=True)
    assert optional.path == "assemble" and optional.parsed is not None
    assert optional.parsed.signals[0].fields.underlying is None  # validation asks the user
    stock = {"msg_type": c("stock_entry"), "ticker": c("not_stated")}
    assert decide(PLAIN, answers=stock, underlying_optional=True).path == "slow_path"
    no_strike = {"role_n1": c("other", 0.95)}
    assert decide(PLAIN, answers=no_strike, underlying_optional=True).path == "slow_path"


def test_t_type_gates_the_message_type_without_loosening_the_field_picks() -> None:
    unsure_type = {"msg_type": c("entry", 0.7)}
    assert decide(PLAIN, answers=unsure_type).path == "slow_path"  # default t_type 0.8
    loose = CFG.model_copy(update={"t_type": Decimal("0.6")})
    assert decide(PLAIN, loose, answers=unsure_type).path == "assemble"
    weak_strike = {**unsure_type, "role_n1": c("strike", 0.7)}
    assert decide(PLAIN, loose, answers=weak_strike).path == "slow_path"  # t_field unchanged


def test_weak_link_is_an_ambiguous_hint_not_an_upgrade() -> None:
    _, decision = run(book=[(P1, "SPY 770C")], links_to=c(str(P1), 0.6))
    assert decision.path == "assemble" and decision.link_ambiguous
    assert decision.parsed is not None
    assert decision.parsed.signals[0].link_hint.target == "ambiguous"
    _, sure = run(book=[(P1, "SPY 770C")], links_to=c(str(P1), 0.95))
    assert sure.parsed is not None and sure.parsed.signals[0].link_hint.target == P1


def test_chatter_is_feed_only_only_above_t_chat() -> None:
    assert run("what a day", msg_type=c("chatter", 0.95))[1].path == "feed_only"
    assert run("what a day", msg_type=c("chatter", 0.85))[1].path == "slow_path"


def test_trim_or_soft_exit_at_middling_intent_is_a_soft_exit() -> None:
    mt = c("trim", 0.5, soft_exit=0.45)
    result, _ = run("might take some here", msg_type=mt, exit_intent=score(2.1))
    assert result.msg_type is MsgType.SOFT_EXIT and result.msg_type_confidence == Decimal("0.95")
    result, _ = run("might take some here", msg_type=mt, exit_intent=score(3.2))
    assert result.msg_type is MsgType.TRIM  # clear partial sell intent: stays uncertain trim


def test_jev_unavailable_routes_to_slow_path() -> None:
    assert route(None, CFG).path == "slow_path"


def test_questions_cover_every_number_candidate_with_no_match_options() -> None:
    context, candidates = ctx(EXAMPLE), find_candidates(EXAMPLE)
    q = build_questions(context, candidates)
    assert {f"role_{x.id}" for x in candidates.numbers} <= q.keys()
    role = q["role_n1"]
    assert isinstance(role, Choice) and set(role.criteria) == set(NUMBER_ROLES)
    for name, no_match in (
        ("ticker", "not_stated"),
        ("expiry", "not_stated"),
        ("links_to", "new_position"),
    ):
        choice = q[name]
        assert isinstance(choice, Choice) and no_match in choice.criteria
    state = build_jev_state(context, candidates)
    assert [x["text"] for x in state["candidates"]["numbers"]] == ["770c", ".95", "769"]


def test_links_to_offers_only_the_resolvers_candidates() -> None:
    """F069: a position the resolver ruled out is never a Jev option."""
    keep, drop = uuid.uuid4(), uuid.uuid4()
    context = ctx("trim half", book=[(keep, "SPY 765P"), (drop, "QQQ 745P")])
    assert [b.id for b in context.link_candidates] == [keep, drop]  # default: the whole book
    narrowed = context.model_copy(update={"link_candidates": context.provider_book[:1]})
    q = build_questions(narrowed, find_candidates("trim half"))["links_to"]
    assert isinstance(q, Choice)
    assert set(q.criteria) == {str(keep), "new_position", "ambiguous"}


EXIT_ANSWERS = {
    "msg_type": c("trim", 0.93),
    "ticker": c("not_stated"),
    "right": c("not_stated"),
    "exit_fraction": c("half", 0.95),
}


def test_plain_fields_say_a_model_judged_them() -> None:
    """F071: msg_type, instrument, stop basis and flags are Jev's judgment, labelled so."""
    _, decision = run()
    assert decision.parsed is not None
    [signal] = decision.parsed.signals
    src = signal.field_sources
    assert isinstance(src["msg_type"], ModelSource)
    assert (src["msg_type"].interpreter, src["msg_type"].question) == ("jev", "msg_type")
    assert src["msg_type"].confidence == Decimal("0.95")
    for name in ("instrument", "stop_basis", "candle_close"):
        assert isinstance(src[name], ModelSource), name


def test_an_exit_fraction_the_text_says_is_literal() -> None:
    _, decision = run("sold half", **EXIT_ANSWERS)
    assert decision.parsed is not None
    source = decision.parsed.signals[0].field_sources["exit_fraction"]
    assert isinstance(source, LiteralSpan) and source.raw == "half"
    assert "sold half"[source.start : source.end] == "half"


def test_an_exit_fraction_the_text_does_not_say_is_a_judgment() -> None:
    _, decision = run("took some here", **EXIT_ANSWERS)
    assert decision.parsed is not None
    assert isinstance(decision.parsed.signals[0].field_sources["exit_fraction"], ModelSource)


def test_the_c52_soft_exit_rule_is_code() -> None:
    text = "might take some here"
    answers = example_answers(
        msg_type=c("trim", 0.5, soft_exit=0.45),
        exit_intent=score(2.1),
        ticker=c("not_stated"),
        right=c("not_stated"),
    )
    mapped = map_answers(answers, ctx(text), find_candidates(text), CFG)
    assert mapped.msg_type is MsgType.SOFT_EXIT and mapped.parsed is not None
    source = mapped.parsed.signals[0].field_sources["msg_type"]
    assert isinstance(source, ResolvedSource) and source.rule == "c52_soft_exit"


def test_a_link_outside_the_candidates_is_ambiguous_not_a_crash() -> None:
    result, _ = run(
        "trim half",
        book=[(P1, "SPY 765P")],
        msg_type=c("trim"),
        ticker=c("not_stated"),
        right=c("not_stated"),
        exit_fraction=c("half"),
        links_to=c(str(uuid.uuid4()), 0.99),
    )
    assert result.parsed is not None
    assert result.parsed.signals[0].link_hint.target == "ambiguous"


# --- F078: why a message is not settled -------------------------------------------------------


def test_unsure_type_is_a_semantic_doubt() -> None:
    _, decision = run(msg_type=c("entry", 0.6))
    assert (decision.path, decision.kind) == ("slow_path", "semantic")


def test_no_answer_is_a_model_failure() -> None:
    assert route(None, CFG).kind == "model_failure"


def test_back_in_with_several_earlier_positions_is_a_review_not_a_model_question() -> None:
    """F078: "back in" names no contract and two closed positions could be meant: which one is
    a person's call (F077), so no other model is asked."""
    text = "back in here"
    context = ctx(text).model_copy(
        update={
            "reentry_candidates": [
                BookEntry(id=uuid.uuid4(), summary="closed SPY 765P exp 2026-10-02"),
                BookEntry(id=uuid.uuid4(), summary="closed QQQ 745P exp 2026-10-02"),
            ]
        }
    )
    answers = example_answers(
        msg_type=c("entry", 0.92),
        ticker=c("not_stated"),
        right=c("not_stated"),
        expiry=c("not_stated"),
    )
    answers = {k: v for k, v in answers.items() if not k.startswith("role_")}
    result = map_answers(answers, context, find_candidates(text), CFG)
    decision = route(result, CFG)
    assert (decision.path, decision.kind) == ("slow_path", "link")
    assert decision.reasons[0].startswith("reentry: 2 earlier position(s)")
    state = build_jev_state(context, find_candidates(text))
    assert [r["summary"] for r in state["reentry_candidates"]] == [
        "closed SPY 765P exp 2026-10-02",
        "closed QQQ 745P exp 2026-10-02",
    ]


def test_a_missing_contract_without_reentry_candidates_stays_semantic() -> None:
    _, decision = run(ticker=c("not_stated"), role_n1=c("other"))
    assert (decision.path, decision.kind) == ("slow_path", "semantic")


# --- F079: the condition gate, explicit ------------------------------------------------------


def fitted(text: str, **answers: object) -> dict[str, object]:
    """Example answers fitted to `text`: one role answer per number candidate found in it."""
    base = example_answers(**answers)  # type: ignore[arg-type]
    base = {k: v for k, v in base.items() if not k.startswith("role_")}
    for cand in find_candidates(text).numbers:
        base.setdefault(f"role_{cand.id}", c("underlying_level"))
    return {**base, **{k: v for k, v in answers.items() if k.startswith("role_")}}


def gate(text: str, **answers: object):  # type: ignore[no-untyped-def]
    result = map_answers(fitted(text, **answers), ctx(text), find_candidates(text), CFG)  # type: ignore[arg-type]
    return result, route(result, CFG)


NO_CONTRACT = {"ticker": c("not_stated"), "right": c("not_stated")}


def test_if_spy_holds_770_then_calls_is_a_conditional_setup() -> None:
    result, decision = gate(
        "if SPY holds 770 then calls",
        msg_type=c("conditional", 0.9),
        has_prose_condition=n(0.95),
        ticker=c("t1", 0.95),
        right=c("call", 0.9),
    )
    assert decision.path == "assemble" and result.slow_reasons == ()
    assert result.parsed is not None and result.parsed.signals[0].msg_type == "conditional"


def test_watching_spy_around_770_is_a_watch() -> None:
    result, decision = gate(
        "watching SPY around 770", msg_type=c("watch", 0.95), ticker=c("t1", 0.95)
    )
    assert decision.path == "feed_only"
    assert result.parsed is not None and result.parsed.signals[0].fields.underlying is not None


def test_spy_looks_strong_is_commentary() -> None:
    _, decision = gate("SPY looks strong", msg_type=c("chatter", 0.95), ticker=c("t1"))
    assert decision.path == "feed_only"


def test_if_this_breaks_im_out_is_a_conditional_instruction_for_a_person() -> None:
    result, decision = gate(
        "if this breaks I'm out",
        msg_type=c("full_exit", 0.9),
        exit_fraction=c("all", 0.9),
        has_prose_condition=n(0.9),
        **NO_CONTRACT,
    )
    assert (decision.path, decision.kind) == ("slow_path", "semantic")
    assert result.slow_reasons[0].startswith("conditional_instruction")


def test_a_doubtful_condition_on_an_entry_is_not_assembled() -> None:
    result, decision = run(has_prose_condition=n(0.3))
    assert decision.path == "slow_path"
    assert any(r.startswith("condition_doubt") for r in result.slow_reasons)
    _, sure = run(has_prose_condition=n(0.1))
    assert sure.path == "assemble"


# --- F080: every question has a reader; the version follows the catalog ---------------------


def test_every_catalog_question_has_a_consumer() -> None:
    from app.interpret.jev.mapping import CONSUMED

    q = build_questions(ctx(EXAMPLE), find_candidates(EXAMPLE))
    fixed = {k for k in q if not k.startswith("role_")}
    assert fixed == set(CONSUMED)
    assert "correction_target" not in q and "is_add" not in q  # no reader: dropped (F080)


def test_the_question_set_version_is_the_catalog_fingerprint() -> None:
    """A wording, option or routing change must move this on purpose (and replay first)."""
    from app.interpret.jev.questions import VERSION

    assert VERSION == "jev_questions@e12e3dadf071"


def test_an_entry_sl_tp1_tp2_signal_assembles_without_review() -> None:
    """The 1 of TP1 and the 2 of TP2 are not offered as numbers any more, so the model has
    nothing it can be unsure about: the signal becomes a trade card, not a review card."""
    text = (
        "@everyone\n\n$TSLA 400C 10/16/26\n\nEntry: 2.10\nSL: 1.50\nTP1: 3.00\nTP2: 4.00\n\n"
        "Small size. Let's get it!"
    )
    _, decision = run(
        text,
        ticker=c("t1", 0.95),
        expiry=c("e1", 0.95),
        stop_basis=c("premium", 0.9),
        role_n1=c("strike", 0.97),
        role_n2=c("entry_price", 0.97),
        role_n3=c("stop_level", 0.95),
        role_n4=c("take_profit_level", 0.95),
        role_n5=c("take_profit_level", 0.95),
    )
    assert decision.path == "assemble", decision.reasons
    assert decision.parsed is not None
    f = decision.parsed.signals[0].fields
    assert [t.value for t in f.tp_levels] == [Decimal("3.00"), Decimal("4.00")]
    assert f.stop_level is not None and f.stop_level.value == Decimal("1.50")
    assert f.entry_price is not None and f.entry_price.value == Decimal("2.10")


def test_a_confidence_doubt_leaves_a_draft_carrying_the_low_confidence() -> None:
    """With no LLM to settle it, the user decides on Jev's own reading."""
    result, decision = run(candle_close=n(0.21))
    assert decision.path == "slow_path" and decision.parsed is None
    assert result.draft is not None
    sig = result.draft.signals[0]
    assert sig.fields.stop_level is not None and sig.fields.candle_close is False
    assert sig.field_confidence["candle_close"] < CFG.t_field


def test_a_doubt_about_what_the_message_says_leaves_no_draft() -> None:
    assert run(has_prose_condition=n(0.9))[0].draft is None
    assert run(signal_count=c("multiple"))[0].draft is None
    assert run(role_n1=c("strike", 0.6), ticker=c("not_stated"))[0].draft is None
