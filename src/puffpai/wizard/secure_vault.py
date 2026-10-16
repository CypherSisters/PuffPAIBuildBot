"""The vault ceremony, without a print statement in it.

The card contract sets a problem this module is the
answer to: the vault's recovery code is generated while the skill applies, it
has to reach the owner exactly once, and it may not appear on the card, in the
answers, in the run log, in the API response or in an exception. So it goes
through the **face carried on the run's context** (``RunContext.show_once``,
set by :meth:`puffpai.wizard.runner.Runner.show_once` for the run that is
drawing the wizard) -- and this module holds the *policy* (how many times a
code is shown, how many wrong attempts, what happens when the owner cannot
write it down right now) while the face holds the I/O.

The face is an argument, never a module global. A global would mean a process
that once drew a card on a terminal answers "yes, somebody can see this" to
every later request, including one that arrived over HTTP from a browser.

The policy is the one ``cli._recovery_ceremony`` already ran, constant for
constant, because a converted skill may change its wording and may not change
what it does.

Two properties worth stating plainly:

* **Nothing here returns, stores or logs the code.** It is a local, it is
  handed to the face, and the function returns a boolean.
* **No face, no vault.** :func:`can_show` is False when nobody has registered,
  and the caller must not create a vault it cannot show the way back into.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

from typing import Any

#: What the owner is told when a run seals a vault with nobody reading the
#: screen. Today's wizard prints this above the code on a ``--yes`` run.
UNCONFIRMED_LINES = (
    "  Nobody typed this recovery code back.",
    "  No one is at the keyboard, so it is printed once, here, and it is now",
    "  in whatever log this output went to. Copy it somewhere safe and keep",
    "  that log private.",
)

NOT_CONFIRMED_LINES = (
    "  The recovery code was not typed back.",
    "  Your vault is locked and works, and your PIN opens it. Nobody wrote",
    "  down a way back in, so if you forget the PIN, what is inside is gone.",
    "  The build record says the code was not confirmed.",
)

SKIP_LINES = (
    "  If you cannot write it down right now, press Enter or type 'skip'.",
    "  The code keeps working either way.",
)

#: Said once when the owner chose not to type the code back. The code on the
#: screen is kept (it still opens the vault), because throwing it away and
#: printing another one is no help to somebody who cannot write one down.
DECLINED_LINES = (
    "  Okay. That code still works, so keep it somewhere safe.",
    "  Nobody typed it back, so the build record says it was not checked.",
    "  If you lose it and forget the PIN, what is inside is gone. To make a",
    "  new code later, open your vault on the dashboard's Vault page and",
    "  press New recovery code.",
)

#: Said above the code when the builder said they are under 13. Wording only:
#: the vault, the code and the rules are the same for everybody.
YOUNG_LINES = (
    "  Ask a grown-up to help you write this code down and keep it safe.",
)

#: Said when every try at typing a code back was wrong and a new one is made.
FRESH_CODE_LINES = (
    "  That did not match, so the code you wrote down may have a mistake in it.",
    "  That code is now dead. Here is a fresh one.",
)


def face_of(ctx: Any) -> Any:
    """The show-once face this run carries, or ``None``. Fail closed.

    :meth:`puffpai.wizard.runner.RunContext.face` is the real check; this
    wrapper exists so a caller holding something context-shaped (a test double)
    gets the same fail-closed answer rather than an attribute error.
    """
    face = getattr(ctx, "face", None)
    if callable(face):
        return face()
    sink = getattr(ctx, "show_once", None)
    if sink is None:
        return None
    if not all(callable(getattr(sink, name, None)) for name in ("show", "ask")):
        return None
    return sink


def can_show(ctx: Any) -> bool:
    """True when this run has a face that can put a value in front of somebody."""
    return face_of(ctx) is not None


def _code_lines(code: str) -> list[str]:
    from puffpai import setup_ops
    return [*(f"  {line}" for line in setup_ops.vault_recovery_notice()),
            "", f"      {code}", ""]


def _unrecorded(lines: tuple[str, ...]) -> list[str]:
    """``lines`` without the sentence about the build record, for a caller
    that keeps no build record (``puffpai vault recover``)."""
    return [line for line in lines if "build record" not in line]


def show_and_confirm(code: str, pin: str, vault: Any, *, sink: Any,
                     non_interactive: bool, young: bool = False,
                     recorded: bool = True) -> bool:
    """Show the code once, ask for it back, and say whether it came back.

    The ``code`` parameter is the only place this value exists in this process
    outside the vault itself. It is never returned and never recorded; the
    caller gets a boolean and writes *that* into the manifest, which is exactly
    what ``vault_manifest_block`` has always carried.

    Two ways not to confirm, treated differently:

    * **Declining** (Enter, or "skip"): the code on the screen is kept - it
      still opens the vault - and the run moves on, recorded as not checked.
      It used to be burned and replaced, three times, which left the owner
      with no code at all.
    * **A wrong answer**: tried again; when every try is wrong the written
      copy is probably wrong, so the code is replaced and shown again.

    ``recorded=False`` drops the lines that say what the build record says,
    for a caller that does not write one. The policy is unchanged.
    """
    from puffpai import cli, setup_ops, zone_vault
    from puffpai.security.vault import VaultError

    if sink is None:
        # Unreachable through the callers in this build -- they check first --
        # and kept because "show a code with nowhere to show it" must fail
        # closed wherever it is called from.
        return False

    top = list(YOUNG_LINES) if young else []
    if non_interactive:
        sink.show([*UNCONFIRMED_LINES, *top, *_code_lines(code)])
        return False

    shows = 0
    while shows < cli._RECOVERY_MAX_SHOWS:
        shows += 1
        sink.show([*top, *_code_lines(code), *SKIP_LINES])

        mismatches = 0
        while mismatches < cli._RECOVERY_MAX_MISMATCHES:
            typed = (sink.ask(f"  {zone_vault.RECOVERY_CONFIRM_PROMPT}")
                     or "").strip()
            if typed.lower() in cli._RECOVERY_ESCAPES or not typed:
                sink.show(list(DECLINED_LINES) if recorded
                          else _unrecorded(DECLINED_LINES))
                return False
            if setup_ops.confirm_vault_recovery(code, typed):
                sink.show(["  That matches. Your vault is locked and you have "
                           "a way back in."])
                return True
            mismatches += 1
            sink.show([f"  {zone_vault.RECOVERY_CONFIRM_FAILED}"])

        if shows >= cli._RECOVERY_MAX_SHOWS:
            break
        try:
            code = setup_ops.rotate_vault_recovery(code, pin, vault=vault)
        except VaultError as exc:
            sink.show([f"  Could not issue a new code: {exc}"])
            break
        sink.show(list(FRESH_CODE_LINES))

    sink.show(list(NOT_CONFIRMED_LINES) if recorded
              else _unrecorded(NOT_CONFIRMED_LINES))
    return False


__all__ = ["DECLINED_LINES", "FRESH_CODE_LINES", "NOT_CONFIRMED_LINES",
           "SKIP_LINES", "UNCONFIRMED_LINES", "YOUNG_LINES",
           "can_show", "face_of", "show_and_confirm"]
