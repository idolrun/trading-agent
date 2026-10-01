"""F033: Appendix A mapping and TRD §4.4 routing, on recorded-shape Jev answers."""

import uuid
from decimal import Decimal

from typesafe_sdk import Choice

from app.interpret.candidates import find_candidates
from app.interpret.jev.mapping import map_answers
from app.interpret.jev.questions import NUMBER_ROLES, build_questions
from app.interpret.jev.state import build_jev_state
from app.interpret.models import MsgType
from app.interpret.route import route
from app.interpret.signal import LiteralSpan
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
    assert f.size_tier is not None and f.size_tier.value == "lotto"
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
        is_add=n(0.45),
        is_correction=n(0.3),
    )
    assert decision.path == "assemble", decision.reasons
    assert decision.parsed is not None
    f = decision.parsed.signals[0].fields
    assert f.stop_basis is None and f.exit_fraction is None and not f.is_add


def test_a_confident_flag_is_still_recorded_without_a_stop() -> None:
    _, decision = run(PLAIN, is_add=n(0.95))
    assert decision.parsed is not None and decision.parsed.signals[0].fields.is_add


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
        "size_tier": c("not_stated"),
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
    q = build_questions(context, candidates, ["default", "lotto", "small"])
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
