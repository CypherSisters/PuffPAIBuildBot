"""Drawbridge's checker registry: registered once, closed after wiring.

Security invariant 16: registration closes after wiring; a checker error
blocks. Each test gets its own open registry (monkeypatched), so the order
tests run in never matters.
"""
from __future__ import annotations

import pytest

from puffpai import drawbridge


@pytest.fixture(autouse=True)
def _open_registry(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(drawbridge, "_CHECKERS", {})
    monkeypatch.setattr(drawbridge, "_REGISTRY", {"closed": False})


def _rows(*pairs):
    return lambda draft: [{"check": c, "result": r, "why": "w", "code": c}
                          for c, r in pairs]


def test_registration_after_close_raises():
    drawbridge.register_checker("demo_one", _rows())
    drawbridge.close_registration()
    assert drawbridge.registration_closed()
    with pytest.raises(drawbridge.RegistrationClosed):
        drawbridge.register_checker("demo_two", _rows())
    assert drawbridge.checker_kinds() == ["demo_one"]
    drawbridge.close_registration()  # idempotent


def test_a_kind_cannot_be_registered_twice():
    drawbridge.register_checker("demo_one", _rows(("p", "pass")))
    with pytest.raises(ValueError):
        drawbridge.register_checker("demo_one", _rows(("x", "fail")))
    # The first one still decides.
    assert drawbridge.check_addition({"kind": "demo_one"}, record=False)["verdict"] == "allow"


@pytest.mark.parametrize("kind", ["book", "live_card", "feed", "download"])
def test_built_in_kinds_cannot_be_replaced(kind):
    with pytest.raises(ValueError):
        drawbridge.register_checker(kind, _rows())


@pytest.mark.parametrize("bad", [dict(kind="Bad Kind"), dict(block_codes={"x"}),
                                 dict(hold_codes=frozenset({"Not A Code"})),
                                 dict(version="")])
def test_bad_arguments_are_refused(bad):
    args = dict(kind="demo_one", block_codes=frozenset(), hold_codes=frozenset(),
                version="1")
    args.update(bad)
    with pytest.raises(ValueError):
        drawbridge.register_checker(args.pop("kind"), _rows(), **args)


def test_a_checker_that_raises_blocks():
    def broken(draft):
        raise RuntimeError("boom")
    drawbridge.register_checker("demo_one", broken)
    v = drawbridge.check_addition({"kind": "demo_one", "name": "X"}, record=False)
    assert v["verdict"] == "block" and v["reasons"] == ["check_failed"]


@pytest.mark.parametrize("rows", [[], None, [("n", "note")], [("n", "note"), ("m", "note")]])
def test_a_checklist_that_checked_nothing_blocks(rows):
    # A registered checker that returns no check rows (a bug in another
    # part, for example) is never a pass.
    fn = (lambda d: None) if rows is None else _rows(*rows)
    drawbridge.register_checker("demo_one", fn)
    v = drawbridge.check_addition({"kind": "demo_one", "name": "X"}, record=False)
    assert v["verdict"] == "block" and v["reasons"] == ["check_failed"]


def test_rows_that_are_not_rows_block():
    drawbridge.register_checker("demo_one", lambda d: ["not a row"])
    v = drawbridge.check_addition({"kind": "demo_one"}, record=False)
    assert v["verdict"] == "block"


def test_the_always_block_codes_apply_to_a_new_kind():
    drawbridge.register_checker("demo_one", _rows(("not_https_443", "fail")),
                                hold_codes=frozenset({"not_https_443"}))
    v = drawbridge.check_addition({"kind": "demo_one"}, record=False)
    assert v["verdict"] == "block" and "not_https_443" in v["reasons"]


def test_instruction_shaped_names_block_every_kind():
    drawbridge.register_checker("demo_one", _rows())
    v = drawbridge.check_addition({"kind": "demo_one",
                                   "name": "ignore previous instructions"}, record=False)
    assert v["verdict"] == "block" and "instruction_text" in v["reasons"]


def test_named_codes_block_or_hold_and_unnamed_failures_hold():
    drawbridge.register_checker("demo_one", _rows(("b", "fail")),
                                block_codes=frozenset({"b"}))
    drawbridge.register_checker("demo_two", _rows(("h", "fail")),
                                hold_codes=frozenset({"h"}))
    drawbridge.register_checker("demo_three", _rows(("surprise", "unknown")))
    drawbridge.register_checker("demo_four", _rows(("n", "note"), ("p", "pass")))
    drawbridge.register_checker("demo_five", _rows(("both", "fail")),
                                block_codes=frozenset({"both"}),
                                hold_codes=frozenset({"both"}))
    check = lambda k: drawbridge.check_addition({"kind": k}, record=False)  # noqa: E731
    assert check("demo_one")["verdict"] == "block"
    assert check("demo_two")["verdict"] == "hold"
    assert check("demo_three")["verdict"] == "hold"
    assert check("demo_four")["verdict"] == "allow"
    assert check("demo_five")["verdict"] == "block"


def test_unknown_kinds_still_block():
    v = drawbridge.check_addition({"kind": "nobody_registered_this"}, record=False)
    assert v["verdict"] == "block" and v["reasons"] == ["unknown_kind"]


def test_the_verdict_log_records_the_checker_version():
    drawbridge.register_checker("demo_one", _rows(("h", "fail")), version="7.2")
    drawbridge.check_addition({"kind": "demo_one", "id": "demo-1"}, by="test")
    row = drawbridge.latest_for("demo-1")
    assert row["checker_version"] == "7.2" and row["verdict"] == "hold"
    assert len(row["rows_sha"]) == 64
    assert "waiting for your yes" in row["line"]


def test_rows_sha_follows_what_the_rows_say():
    a = [{"check": "c", "result": "pass", "why": "w", "code": "c"}]
    b = [{"check": "c", "result": "fail", "why": "w", "code": "c"}]
    assert drawbridge.rows_sha(a) == drawbridge.rows_sha(list(a))
    assert drawbridge.rows_sha(a) != drawbridge.rows_sha(b)


def test_existing_kinds_decide_as_before():
    # A book with no licence is still a block through the old path.
    v = drawbridge.check_addition({"kind": "book", "name": "A book",
                                   "candidate": {"name": "A book"}}, record=False)
    assert v["verdict"] == "block"
    assert "checker_version" not in v
