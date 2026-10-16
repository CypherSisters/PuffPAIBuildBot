"""Drawbridge's check of an app's actions (web recipes), before the AI may use one.

A recipe is DATA made by Toolsmith's compiler from the app itself: named steps
from a closed list, static selectors, typed arguments. Every recipe gets a
verdict, ``allow`` or ``block``, with one row per rule:

R1  shape: name, plain words (``says``), effect in reads/changes/removes, bounds
R2  steps only from the closed list the runner knows
R3  selectors are static strings from a safe character set
    + "selector parses": each one is run through ``querySelectorAll`` on the copy
R4  arguments flow only into values, every one declared and typed
R5  changes and removes carry a check of their result; reads carry no writing step
R6  answering the app's "are you sure?" box only in a removes recipe
R7  storage keys it reads are the app's own (the scan's, or ones the app itself
    wrote on the check copy)
R8  words the owner and the AI read are not orders, and carry no banned word:
    the action's words, every argument's words and name, and every choice in
    an argument's list (a select's option texts come from the app). Each is
    short (a choice at most 6 words and 48 characters, words at most 10 words
    and 80), has no sentence marks, and does not speak to the AI reading the
    tool list (:func:`label_ok`); at most 40 choices. Each is read in its
    folded form (NFKC), and one with a hidden character (control, format,
    private) or a word mixing Latin with Cyrillic or Greek letters is refused.
    No word check can tell every order from a real option, so a select's
    choices are never in the AI's tool list at all: the AI passes the owner's
    own words and the run matches them to one choice
    (:func:`puffpai.webapps.actions.schema_of`, ``resolve_choice``). R8 on a
    choice is the second wall, not the first
R9  a file argument is a bare file name
R10 a test run passes on a throwaway copy (its own address, empty storage, every
    outside host off), with evidence from the stored data (an add runs twice:
    a first run on the empty copy, then the measured one)
R11 the effect is PROVEN by diffing the stored data before and after that run:
    ``changes`` only when the diff adds records or edits fields of the one record
    matched by the arguments; a deleted key or record, a shorter list, a whole
    value replaced, or more than one record edited means ``removes``; so does a
    control whose words say delete, remove, reset, clear, import, replace,
    restore, erase, wipe or empty, and so does answering a dialog. The action's
    effect is the stricter of declared and proven. A reads recipe that changed
    anything is blocked.
R12 provenance: ``by`` in {compiler, reader}, the current compiler version and the
    current app fingerprint; a recipe written by hand (or edited after it was
    made) is refused. Those fields are public, so on their own anyone who can
    write the state folder could recompute them: what makes R12 hold is the
    seal :mod:`puffpai.webapps.recipes` adds when it saves a checked recipe
    (a keyed hash under a key kept outside the state folder) and checks on
    every load, together with R1-R9 run again and the effect floor
    (:func:`static_effect_floor`).

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Any, Callable, Optional

COMPILER_VERSION = "r3.1"
PROVENANCE_BY = frozenset({"compiler", "reader"})
CHECKER_VERSION = "r3.1"

VERBS = frozenset({"click", "click_text", "click_in_row", "fill", "fill_in_row", "choose",
                   "choose_in_row", "check_in_row", "key", "wait", "wait_for",
                   "accept_next_dialog", "read_rows", "read_storage", "set_files"})
WRITES = frozenset({"fill", "fill_in_row", "choose", "choose_in_row", "check_in_row",
                    "set_files", "accept_next_dialog", "click", "click_text", "click_in_row",
                    "key"})
SAFE_SEL = re.compile(r"^[A-Za-z0-9#.\[\]=^$*~|'\"():,> _+-]{1,200}$")
BAD_SEL = re.compile(r"javascript:|\\|<|`|\{\{|\$\{|url\(", re.I)
BANNED_WORDS = re.compile(r"\b(doors?|beta|warts?)\b|—", re.I)
DESTRUCTIVE = re.compile(r"\b(eliminar|borrar|apagar|excluir|supprimer|effacer|elimina|cancella|"
                         r"l[öo]schen|entfernen|удалить|очистить|сбросить|삭제|删除|清除|削除|"
                         r"delete|remove|reset|clear|import|replace|restore|erase|wipe|"
                         r"empty|trash|discard|purge)", re.I)
KEYS_ALLOWED = frozenset({"Enter", "Tab", "Escape"})
EFFECT_ORDER = {"reads": 0, "changes": 1, "removes": 2}
_ID_FIELDS = ("id", "_id", "uuid", "key")


def _orders(text: str) -> bool:
    from puffpai import drawbridge
    return bool(drawbridge.instruction_shaped(text))


#: Words aimed at the AI that reads the tool list, not at a person using the
#: app: they have no business in a field's label or a select's choices. The
#: general orders checks (Drawbridge, artifact_review) run as well. Kept to
#: phrases, so ordinary labels ("Special instructions", "Writing prompt",
#: "Permission slip", "Override price", "Assistant coach") still pass.
_AT_THE_AI = re.compile(
    r"\b(?:(?:the|you|dear|hey)\s+(?:ai\s+)?assistant|assistant\s*(?:note|instruction|message)s?"
    r"|note\s+to\s+(?:the\s+)?(?:assistant|ai|model|bot)"
    r"|llms?|chat\s?bots?|language\s+models?|ai\s+(?:model|assistant|agent)s?"
    r"|jailbreak|system\s+(?:prompt|message|note)|(?:new|hidden|secret|real)\s+instructions?"
    r"|(?:ignore|disregard|forget)\s+(?:the\s+|all\s+|any\s+)?(?:previous|prior|above|earlier|"
    r"other|these|those|your|my)\b"
    r"|you\s+(?:must|should|shall|will|need\s+to|have\s+to|are\s+(?:allowed|told|required))"
    r"|(?:always|never|must)\s+(?:call|pick|choose|use|select|run|remove|delete|say|answer|reply|tell)"
    r"|(?:already|has|have)\s+(?:said|says|agreed|approved|confirmed|consented)"
    r"|said\s+yes|pre-?approved|without\s+asking"
    r"|tool\s*(?:call|choice)|function\s*call|remove_all|delete_all)\b", re.I)
#: Short orders that need no sentence marks: a speaker tag ("AI: ..."), a
#: claim that the owner already agreed, a request to skip her yes, a call to
#: a tool, or removing everything at once. Also phrases only, never one word.
_SHORT_ORDERS = re.compile(
    r"\b(?:ai|model|bot|system|assistant|admin|administrator|developer|agent|operator|user)\s*:"
    r"|\b(?:owner|user|admin)(?:'s)?\s+(?:has\s+|had\s+|already\s+|really\s+)?"
    r"(?:approved|approves|agreed|agrees|consented|consents|confirmed|confirms|said|says|wants|"
    r"allowed|allows|ok'?d|okayed|gave|is\s+(?:ok|fine|happy))\b"
    r"|\b(?:approved|confirmed|agreed|allowed|authori[sz]ed|ok'?d|okayed|signed\s+off)\s+by\s+"
    r"(?:the\s+)?(?:owner|user|admin|you)\b"
    r"|\b(?:ignore|disregard|forget|bypass|override|skip)\s+(?:the\s+|her\s+|his\s+|their\s+)?"
    r"(?:owner|user|confirmations?|confirm|checks?|approvals?|asking|questions?|yes|safety|rules?)\b"
    r"|\b(?:do\s+not|don'?t|never|no\s+need\s+to|without)\s+(?:ask|asking|check|checking|confirm|"
    r"confirming|confirmation|wait|waiting)\b"
    r"|\b(?:call|invoke|trigger|use|run|execute|pick|choose|select)\s+(?:the\s+|this\s+|that\s+)?"
    r"(?:remove|delete|erase|wipe|tool|action|function)s?\b"
    r"|\b(?:remove|delete|erase|wipe|clear|drop|purge)\s+(?:all|every|everyone|everything|each|"
    r"the\s+whole)\b"
    r"|\bfor\s+(?:every|each|all|any)\s+(?:messages?|requests?|answers?|replies|reply|turns?|"
    r"questions?|calls?|times?)\b"
    r"|\byes\s+to\s+(?:all|every|everything|anything)\b"
    r"|\b(?:system|admin|developer|god|debug|root)\s+(?:override|mode|access)\b", re.I)
#: Claims that someone already said yes, asks to go on without her yes,
#: removing a whole list or everyone, and "pick me" orders. Phrases only:
#: "Approved", "Permission slip", "Delete draft", "Select all" still pass.
_CONSENT_CLAIMS = re.compile(
    r"\b(?:pre\s*)?(?:approved|approves|accepted|accepts|consented|consents|agreed|agrees|"
    r"authori[sz]ed|authori[sz]es|okayed|ok'?d|confirmed|confirms|allowed|allows|permitted|"
    r"permits|cleared|signed\s+off)\s+(?:on\s+|to\s+|with\s+)?(?:the\s+|this\s+|that\s+|a\s+|"
    r"any\s+|all\s+|every\s+)?(?:it|this|that|these|them|removal|removals|removing|remove|"
    r"deletion|deletions|deleting|delete|erasing|erase|wiping|wipe|clearing|change|changes|"
    r"everything|all|action|actions|request|run|going\s+ahead)\b"
    r"|\b(?:pre\s*)?(?:approved|confirmed|agreed|allowed|authori[sz]ed|ok'?d|okayed|signed\s+off|"
    r"accepted|permitted|cleared|consented)\s+(?:to\s+)?by\s+\w"
    r"|\bpre\s*(?:approved|authori[sz]ed|confirmed|cleared|agreed|allowed)\b"
    r"|\b(?:approved|authori[sz]ed|confirmed|consented|agreed|cleared|okayed|allowed)\s+already\b"
    r"|\balready\s+(?:authori[sz]ed|allowed|permitted|cleared|okayed|accepted|granted)\b"
    r"|\b(?:owner|user|admin|she|he|they|household|family|boss)(?:'s)?\s+"
    r"(?:has\s+|had\s+|already\s+|is\s+|was\s+)?(?:accepted|accepts|permits|permitted|okay|ok|"
    r"fine|happy|good)\b"
    r"|\b(?:is|are|was|were|am|be)\s+(?:totally\s+|fully\s+|already\s+)?(?:ok|okay|fine|happy|good|"
    r"cool|alright|all\s+right)\s+with\s+(?:\w+\s+)?(?:it|this|that|removal|removing|remove|"
    r"deletion|deleting|delete|erasing|wiping|changes?|everything|all|going\s+ahead)\b"
    r"|\b(?:said|says|gave|gives|given)\s+(?:the\s+|her\s+|his\s+|their\s+)?(?:go\s*ahead|yes|ok|"
    r"okay|green\s+light|permission|consent|approval|the\s+nod)\b"
    r"|\b(?:consent|permission|approval|authori[sz]ation|go\s*ahead|clearance|green\s+light|"
    r"sign\s*off)\s+(?:(?:is|was|has|have|been|already)\s+)*(?:given|granted|received|obtained|"
    r"confirmed|provided|done|on\s+file)\b"
    r"|\bno\s+(?:need\s+(?:for|to)\s+)?(?:confirmation|approval|consent|permission|yes|checks?|"
    r"asking|questions?|review)\s+(?:is\s+|are\s+)?(?:needed|required|necessary)\b"
    r"|\b(?:confirmation|approval|consent|permission|yes|checks?|asking|review)\s+(?:is\s+|are\s+)?"
    r"(?:not|never)\s+(?:needed|required|necessary)\b"
    r"|\bwithout\s+(?:(?:her|his|their|the|an?|any|your|my|our)\s+)?(?:owner'?s?\s+)?(?:yes|ok|"
    r"okay|approval|consent|permission|confirmation|confirming|checking|asking|questions?|review)\b"
    r"|\b(?:remove|delete|erase|wipe|clear|drop|purge|empty|trash|nuke|destroy)\s+(?:out\s+)?"
    r"(?:all|every\w*|each|any\w*|everybody|everyone|anyone|anybody|them|(?:the\s+)?(?:whole|"
    r"entire|full)|(?:the|this|that|my|your|her|his|our|their)\s+(?:\w+\s+){0,2}(?:list|lists|"
    r"data|records|entries|items|table|database|storage))\b"
    r"|\b(?:choose|pick|select|use|call|run|click|press|tap)\s+(?:me|it|here|this\s+(?:one|"
    r"option|choice|tool|action))\b"
    r"|\b(?:choose|pick|select|click|press|tap)\s+this\s*$"
    r"|\b(?:choose|pick|select|use|call|run|click|press|tap)\b.*\b(?:always|(?:every|each)\s+"
    r"(?:single\s+)?time)\b", re.I)
#: Words that take something away, hand something out, or change who may do
#: what. Beside "also", "then", "as well" and the like, or twice in one text,
#: they ask for one more change on top of the one the owner asked for. Words
#: that only fill in or save ("press save", "pick a date then a time") are
#: left alone.
_REMOVING = (r"(?:remov\w*|delet\w*|eras\w*|wip\w*|clear\w*|drop\w*|purg\w*|destroy\w*|"
             r"cancel\w*|uninvit\w*|kick\w*|ban|bans|bann\w*|empt\w*|trash\w*|discard\w*|"
             r"reset\w*|unsubscrib\w*|nuk\w*)")
_REMOVING_RX = re.compile(r"\b" + _REMOVING + r"\b", re.I)
_SEND_OUT_RX = re.compile(
    r"\b(?:send|share|forward|email|post|upload|export|leak|give|copy|mail|text|transfer)\w*\b"
    r".*\b(?:to|with)\s+\S", re.I)
#: A word that hands things out to someone, alone (no "to" needed).
_SEND_WORD_RX = re.compile(
    r"\b(?:send|share|forward|e-?mail|mail|text|sms|message|post|upload|export|download|"
    r"print|publish|tweet|dm|ping|notify|broadcast|leak|sync|copy|copies|call|invite|"
    r"distribute|circulate|blast|announce)\w*\b", re.I)
#: A word for many people or records.
_MANY_RX = re.compile(r"\b(?:all|every\w*|each|entire|whole|any(?:one|body)|others|"
                      r"contacts|guests|people|rows|records|list|lists)\b", re.I)
_POWER_RX = re.compile(r"\b(?:admins?|owners?|access|rights|privileges?|permissions?|roles?|"
                       r"promot\w*|approv\w*|confirm\w*|consent\w*|grant\w*)\b", re.I)
#: A verb after "can", "may" or "could" says who is allowed ("Everyone can
#: edit"), not what to do; it is read as a plain word.
_ABILITY = re.compile(r"\b(?:can|may|could)\s+(?:not\s+)?\w+", re.I)
#: Claims that a yes may be taken for granted ("Assume consent", "This counts
#: as her yes", "Treat this as confirmed", "Yes means remove all"), in any
#: kind of text.
_INFERRED_CONSENT = re.compile(
    r"\b(?:assum\w*|treat\w*|consider\w*|count(?:s|ed)?|deem\w*|tak(?:e|es|en)|regard\w*|pretend\w*|"
    r"suppos\w*|presum\w*|interpret\w*|read)\s+(?:\w+\s+){0,3}?(?:as\s+(?:\w+\s+){0,2}?)?"
    r"(?:yes|consent\w*|agree[sd]?|agreement|approv\w+|confirm\w*|permission|ok|okay|okayed|"
    r"allowed|authori[sz]\w+|granted|go\s*ahead)\b"
    r"|\b(?:yes|consent|approval|permission|agreement)\s+(?:means|=|implies|is\s+(?:implied|assumed|"
    r"presumed|a\s+given)|was\s+(?:implied|assumed|presumed)|counts)\b"
    r"|\b(?:consent|approval|permission|agreement|yes)\s+(?:is\s+|was\s+)?(?:implied|assumed|presumed)\b"
    r"|\b(?:counts|count|means|mean)\s+as\s+(?:a\s+|an\s+|the\s+|her\s+|his\s+|their\s+|your\s+|my\s+)?"
    r"(?:yes|consent|approval|permission|ok|okay|go\s*ahead)\b", re.I)
#: Asking for one more change on top of the one asked for ("Also remove Leo",
#: "Delete Sam as well", "Remove guest then remove Leo"), in any kind of text.
_ONE_MORE = re.compile(
    r"\b(?:also|additionally|afterwards?|then|and\s+then|as\s+well|in\s+addition|too|plus|"
    r"next|subsequently|after\s+that|follow(?:ing)?\s+up|later)\b", re.I)
#: Asking the AI to drop what it was told, in any kind of text.
_DROP_GUIDANCE = re.compile(
    r"\b(?:forget|ignore|disregard|drop|discard|override|bypass|abandon)\s+(?:\w+\s+){0,2}?"
    r"(?:guidance|instructions?|rules?|context|directions?|prompts?|guidelines?|policy|policies|"
    r"limits?|restrictions?|constraints?|safeguards?|guardrails?|programming|training)\b"
    r"|\b(?:earlier|previous|prior|above|original|old|hidden|secret|real|true)\s+(?:guidance|"
    r"instructions?|directions?|prompts?|guidelines?|programming)\b", re.I)

#: Characters that show as blank but are not a space (NFKC keeps them), so
#: they can join two words while the English patterns see one.
_BLANK_LOOKALIKES = frozenset("\u115f\u1160\u3164\uffa0\u2800\u17b4\u17b5")
_LEET = {"0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s",
         "|": "l"}
#: Latin letters (small capitals, dotless i) that read like a plain one.
_LATIN_LOOKS = str.maketrans({"\u0131": "i", "\u0237": "j", "\u0251": "a", "\u0261": "g",
                              "\u026a": "i", "\u029f": "l", "\u1d0f": "o", "\u0280": "r"})


def _match_forms(folded: str) -> list[str]:
    """The forms the order patterns read: marks stripped (an accent cannot
    split "ignore"), hyphens, underscores and dots read as spaces, spaced-out
    letters joined ("o w n e r"), and digits that stand in for letters in a
    word that has letters too ("0wner")."""
    import unicodedata
    t = unicodedata.normalize("NFD", folded)
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn")
    t = unicodedata.normalize("NFC", t).translate(_LATIN_LOOKS)
    t = re.sub(r"[_\-\u2010-\u2015.\u00b7\u2022]+", " ", t)
    t = " ".join(t.split())
    t = re.sub(r"\b(?:\w )+\w\b", lambda m: m.group(0).replace(" ", "")
               if m.group(0).count(" ") >= 2 else m.group(0), t)
    forms = [t]
    for one in ("l", "i"):
        table = str.maketrans(dict(_LEET, **{"1": one}))
        leet = re.sub(r"\S+", lambda m: m.group(0).translate(table)
                      if re.search(r"[^\W\d_]", m.group(0)) and re.search(r"[0-9@$|]", m.group(0))
                      else m.group(0), t)
        if leet not in forms:
            forms.append(leet)
    return forms


#: Marks a label or a choice never needs; a sentence to the AI usually does.
_SENTENCE_MARKS = re.compile(r"[;!?\"`<>{}\[\]\\\n\r\t\x00-\x1f\x7f]")
#: (most words, most characters) per kind of text.
LABEL_LIMITS = {"choice": (6, 48), "words": (10, 80), "name": (6, 40)}
MAX_CHOICES = 40


def _mixed_script(text: str) -> bool:
    """True when one word mixes Latin letters with letters of any other
    script (Cyrillic, Greek, Armenian, Cherokee and more have look-alike
    letters that dodge the English patterns)."""
    import unicodedata
    for word in text.split():
        scripts = set()
        for ch in word:
            if ch.isalpha():
                scripts.add(unicodedata.name(ch, "?").split(" ", 1)[0])
        if "LATIN" in scripts and len(scripts) > 1:
            return True
    return False


def label_ok(text: Any, kind: str = "words") -> bool:
    """True when a short text the AI will read (an action's or argument's
    words, an argument's name, or one choice of a select) is plain and short
    and does not read like orders (R8)."""
    if not isinstance(text, str):
        return False
    import unicodedata
    # control, format (zero-width, direction), private and unassigned
    # characters hide words; a label never needs them
    if any(unicodedata.category(ch)[0] == "C" or ch in _BLANK_LOOKALIKES
           or unicodedata.category(ch) in ("Zl", "Zp") for ch in text):
        return False
    # every check reads the folded form, so fullwidth or styled letters
    # ("\uff21lways") cannot slip an order past the English patterns
    folded = unicodedata.normalize("NFKC", text)
    if _mixed_script(folded):
        return False
    most_words, most_chars = LABEL_LIMITS.get(kind, LABEL_LIMITS["words"])
    shown = folded.replace("_", " ") if kind == "name" else folded
    words = shown.split()
    if not words or len(words) > most_words or len(text) > most_chars:
        return False
    if _SENTENCE_MARKS.search(text) or BANNED_WORDS.search(shown):
        return False
    if kind == "choice" and shown.rstrip().endswith("."):
        return False
    from puffpai import artifact_review
    for form in [shown] + _match_forms(shown):
        if _AT_THE_AI.search(form) or _SHORT_ORDERS.search(form) \
                or _CONSENT_CLAIMS.search(form) or _INFERRED_CONSENT.search(form) \
                or _DROP_GUIDANCE.search(form) or _one_more_change(form) or _orders(form) \
                or artifact_review.reads_like_orders(form):
            return False
    return True


#: Words that change a record, a setting or a person's place: beside
#: "also", "too" or "as well", or after "then", "next" or "and", they ask for
#: one more change on top of the one the owner asked for ("Also set Leo to
#: declined", "Then edit every row", "Pick this and remove Leo"). Only the
#: forms that do something (set, sets, setting) are listed, never the "-ed"
#: form that only describes ("Show archived items too" is left alone), and
#: never a word that only fills in or saves (press, save, choose, sort).
_CHANGE_VERBS = ("set", "mark", "change", "rename", "edit", "update", "modify", "alter",
                 "replace", "overwrite", "decline", "reject", "deny", "refuse", "accept",
                 "invite", "uninvite", "add", "move", "switch", "turn", "toggle", "disable",
                 "enable", "make", "assign", "unassign", "block", "unblock", "lock", "unlock",
                 "publish", "unpublish", "archive", "unarchive", "restore", "revert", "undo",
                 "pay", "flag", "unflag", "mute", "unmute", "join", "leave", "subscribe",
                 "book", "order", "buy", "reply", "respond", "notify", "message", "approve",
                 "grant", "revoke", "promote", "demote", "transfer", "merge", "split",
                 "resend", "reshare", "repost",
                 # A second wall only (the joiner rules below
                 # need no change word at all). Only words that are never a
                 # thing's name: "close", "refund", "charge", "pin", "tag" and
                 # "upgrade" are left out ("Save and close", "Returns and
                 # refunds", "Fees and charges", "Password and PIN").
                 "bump", "reopen", "reassign", "rebook", "reschedule", "unconfirm",
                 "waitlist", "untag", "unpin", "unstar")


def _verb_forms(verbs: tuple) -> list[str]:
    out: set[str] = set()
    for v in verbs:
        out.update({v, v + "s", v + "es", v + "ing"})
        if v.endswith("e"):
            out.add(v[:-1] + "ing")
        if v.endswith("y"):
            out.add(v[:-1] + "ies")
        if len(v) >= 3 and v[-1] not in "aeiouwy" and v[-2] in "aeiou" and v[-3] not in "aeiou":
            out.add(v + v[-1] + "ing")
    return sorted(out, key=len, reverse=True)


def _done_forms(verbs: tuple) -> list[str]:
    """The "-ed" forms, read only after "get", "is" or "be" ("Sam gets
    declined as well"), never alone ("Show archived items too")."""
    out: set[str] = {"set", "paid", "made", "sent", "resent", "bought", "left", "split",
                     "undone"}
    for v in verbs:
        out.add(v + "d" if v.endswith("e") else v + "ed")
        if v.endswith("y") and v[-2] not in "aeiou":
            out.add(v[:-1] + "ied")
        if len(v) >= 3 and v[-1] not in "aeiouwy" and v[-2] in "aeiou" and v[-3] not in "aeiou":
            out.add(v + v[-1] + "ed")
    return sorted(out, key=len, reverse=True)


_CHANGE_RX = re.compile(r"\b(?:" + "|".join(_verb_forms(_CHANGE_VERBS)) + r")\b", re.I)
_DONE_RX = re.compile(r"\b(?:gets?|got|getting|gotten|be|is|are|was|were|been|being|becomes?)\s+"
                      r"(?:" + "|".join(_done_forms(_CHANGE_VERBS)) + r"|" + _REMOVING + r")\b",
                      re.I)
_ANY_CHANGE_RX = re.compile(_CHANGE_RX.pattern + r"|\b" + _REMOVING + r"\b", re.I)
#: "Also"-like words: one more change wherever the change word stands.
_ALSO = re.compile(r"\b(?:also|additionally|as\s+well(?:\s+as)?|in\s+addition|too|plus|besides|"
                   r"moreover|furthermore|further|likewise|similarly|on\s+top|"
                   r"same\s+(?:for|goes|with)|do\s+the\s+same)\b", re.I)
#: "Then"-like words: they join two steps. The step after them (or a step
#: before one at the very end, "Decline Leo later") is one more change when
#: it holds a change word; filling in then saving ("Set a reminder then
#: save") is one step.
_THEN = re.compile(r"(?<!\w)(?:and\s+then|then|next|afterwards?|after\s+that|after\s+which|"
                   r"after\s+this|after|before\s+that|subsequently|follow(?:ing)?\s+up|"
                   r"following\s+that|later|once\s+done|when\s+done|finally|lastly|"
                   r"meanwhile|and|&|\+)(?!\w)", re.I)
#: One word that only reads, before "and" ("Read and accept terms"): one
#: step, not two.
_READ_FIRST = frozenset({"read", "review", "check", "view", "see", "sign", "agree", "understand",
                         "acknowledge"})


#: Words that only ever join one more step to a step, never part of an
#: option's own name ("Additionally close the event", "Moreover bump Sam",
#: "Do the same for Leo", "And then refund every guest"): refused with or
#: without a change word, as no list of change words is ever complete.
_ALWAYS_JOIN = re.compile(
    r"\b(?:additionally|moreover|furthermore|likewise|similarly|afterwards?|thereafter|"
    r"subsequently|in\s+addition|and\s+also|and\s+then\b(?=\W*\w)|after\s+(?:that|which|this)|"
    r"following\s+(?:that|this)|do\s+the\s+same|same\s+(?:for|goes|with)|"
    r"same\s+here\b(?=\W*\w)|do\s+likewise|"
    # words that join a second thing or a second step to the
    # first ("Paid along with Leo", "Thereupon delete Sam", "Prior to that,
    # delete Sam")
    r"thereupon|whereupon|prior\s+to\s+(?:that|this)|until\s+then(?=\W+\w)|"
    r"upon\s+(?:that|this)|along\s+with|together\s+with|alongside|in\s+addition\s+to|"
    r"simultaneously|concurrently)\b", re.I)
#: An "also"- or "then"-like word with a written mark before or after it
#: and more words after it: a second clause ("Paid, then charge Sam again",
#: "Also: Leo declined", "Attending; also kick Sam"). A bare "and" after a
#: comma is left alone ("Name, email, and phone").
_MARKED_STEP = re.compile(
    r"(?:^\s*(?:also|too|plus|then|next|later|finally|lastly|now|after|second|third|besides)"
    r"\s*[,;:–—-]"
    r"|(?:[,;:.!–—(\[/|>]|\s[-‐‑])\s*"
    r"(?:also|too|plus|then|next|later|finally|lastly|now|after|on\s+top|besides|second|"
    r"third)\b"
    r"\s*[,;:–—-]?)\s*(\w)", re.I)
#: Words after a marked "later"-like word that never ask for a step ("Later,
#: thanks").
_COURTESY = frozenset({"thanks", "thank", "you", "please", "maybe", "sure", "ok", "okay",
                       "then", "thx"})
#: Words that join one more step wherever they stand: "Paid
#: as well as Leo", "Paid followed by hiding Sam", "Secondly hide Sam",
#: "Paid, while you're at it hide Sam", "In the meantime nudge Leo".
_STEP_ANYWHERE = re.compile(
    r"\b(?:as\s+well\s+as|followed\s+by|besides|secondly|thirdly|lastly|"
    r"while\b.{0,16}\bat\s+it|on\s+top\s+of|in\s+the\s+meantime|meanwhile|"
    r"at\s+the\s+same\s+time)\b"
    # a leading "when this is done" part, then a step ("When finished, ping
    # all", "Once paid: hide Sam")
    r"|^\s*(?:when|whenever|once|after|before|as\s+soon\s+as|if|upon)\b[^,;:.!]{0,40}"
    r"[,;:.!–—]\s*\w", re.I)
#: "Also", "too" or "as well" at the very end ("Hide Sam also", "Leo too",
#: "Paid, also").
_TRAILING_ALSO = re.compile(r"\b(?:also|too|as\s+well|likewise)\W*$", re.I)
#: A "then" with a word before it ("Paid then hide Sam", "Paid / then hide
#: Sam"); "Then and now" has none.
_MID_THEN = re.compile(r"(?<=\w)[^\w]*\s*\bthen\b", re.I)
#: "plus" inside a text with two or more words after it ("Paid plus nudge
#: Leo"; "Adults plus kids" is a name).
_MID_PLUS = re.compile(r"\w[^\w]*\s+plus\s+\w+\W+\w", re.I)
#: The only words that may stand beside a bare "also", "too" or "then"
#: without a written mark and still make one step: filling in, saving,
#: showing, and the little words between ("Set a reminder then save", "Show
#: archived items too", "Also send a copy", "Also known as", "Me too"). Any
#: other word, a name above all ("Also hide Sam", "Leo too", "Paid then
#: nudge Leo"), makes it one more step (fail closed: no list of change words
#: is ever complete, so the list is of words that are known to be one step).
_ONE_STEP_WORDS = frozenset({
    "a", "an", "the", "it", "this", "me", "us", "my", "your", "our",
    "add", "set", "pick", "choose", "select", "sort", "press", "save", "submit", "enter",
    "type", "tap", "click", "show", "hide", "view", "see", "read", "send", "print",
    "include", "known", "as", "ran", "copy", "note", "label", "date", "time", "reminder",
    "items", "item", "archived", "done", "ok", "continue", "confirm", "and", "now",
})


def _one_step(words: list) -> bool:
    return all(w.casefold() in _ONE_STEP_WORDS for w in words)
#: "Buy now, pay later": each part is one change word and a word for when
#: (or "me", "us", "it"), naming no one.
_WHEN_WORDS = frozenset({"now", "later", "then", "today", "tomorrow", "soon", "me", "us", "it"})
#: "Then"-like words that are not "and": with an "also"-like word in the
#: same text they make two steps ("Then do it for Leo too").
_THEN_NOT_AND = re.compile(r"(?<!\w)(?:then|next|afterwards?|after|later|finally|lastly)(?!\w)",
                           re.I)
#: Words that may stand between a change word and a "then"-like word at the
#: very end without naming a person or record ("Pay me later").
_SELF_WORDS = frozenset({"me", "us", "it"})
#: Words for when, and the small words between them, that may stand after a
#: change word at the very end and still name no one ("Pay now or later").
_SELF_OR_WHEN = _SELF_WORDS | _WHEN_WORDS | frozenset({"or"})

#: Where a label is split into parts: a comma, a semicolon, a
#: colon, a bar, a slash, an arrow, a bullet, a dash, or the end of a
#: sentence ("Paid, delete Sam", "Paid \u2192 delete Sam", "Paid. Leo next").
_PART_BREAK = re.compile("\\s*(?:[,;:|>/\u00bb\u2192\u21d2\u27a1\u2794\u00b7\u2022\u2023"
                         "\u25b8\u25ba\u2013\u2014]|\\s-+\\s|\\.(?:\\s|$))\\s*")
#: Words that, in a part after the first, join a second step or a second
#: person to the first ("Paid, Leo next", "Paid, ditto Leo", "Paid, btw
#: delete Sam", "Paid, one more thing").
_LATER_PART_JOIN = frozenset({
    "next", "again", "ditto", "more", "btw", "ps", "simultaneously", "concurrently",
    "eventually", "ultimately", "end", "extra", "bonus", "oh", "thing", "also", "then",
    "too", "plus", "finally", "lastly", "besides", "meanwhile", "afterwards", "afterward",
    "after", "before", "thereupon", "thereafter", "subsequently", "same"})
#: Small words a title-case label leaves in small letters ("Terms and
#: Conditions"); any other small-letter word means capitals are names.
_TITLE_SMALL = frozenset({"a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with",
                          "at", "by", "from", "as", "per", "vs"})
#: The parts "Buy now, pay later" and "Order now, pick up later" are made of:
#: one word, then words for when (and "up", "out", "back").
_WHEN_PART_WORDS = _WHEN_WORDS | frozenset({"up", "out", "back", "or"})
#: A leading "when this is done" part with no mark after it ("When paid
#: delete Sam", "Once paid delete Sam", "As soon as paid delete Sam").
_LEADING_WHEN = re.compile(r"^\s*(?:when|whenever|once|after|before|as\s+soon\s+as|if|upon|"
                           r"until|the\s+moment)\b", re.I)
#: A described option that starts with "also" ("Also available in blue",
#: "Also known as"), never a second step.
_ALSO_DESCRIBES = re.compile(r"^\s*also\s+(?:available|known|called|sold|fits|works|comes|"
                             r"included|offered|served)\b", re.I)
_DONE_WORDS = frozenset(w.casefold() for w in _done_forms(_CHANGE_VERBS))


def _name_cap(word: str) -> bool:
    """A word written like a name: a capital, then small letters ("Leo");
    never a format or a short form in capitals ("YYYY", "MM", "PS")."""
    return len(word) > 1 and word[0].isupper() and any(ch.islower() for ch in word[1:])


def _capital_names(words: list) -> bool:
    """True when a word that is not the first is capitalized (a name) and
    the text is not written in title case ("Paid and ping Leo" names Leo;
    "Terms and Conditions" is a title)."""
    if not words:
        return False
    small = [w for w in words if w[:1].islower() and w.casefold() not in _TITLE_SMALL]
    return bool(small) and any(_name_cap(w) for w in words[1:])


def _when_parts(parts: list) -> bool:
    """True for "Ship now, pay later", "Order now, pick up later", "Not now,
    later": every part is at most three words, all but the first a word for
    when, and nothing is taken away."""
    for p in parts:
        ws = re.findall(r"\w+", p)
        if not ws or len(ws) > 3 or _REMOVING_RX.search(p) \
                or not all(w.casefold() in _WHEN_PART_WORDS for w in ws[1:]):
            return False
    return True


def _yes_no_hint(plain: str, part: str) -> bool:
    """True for the hint a yes/no box carries ("yes to tick it, no to clear
    it"): the text starts with "yes", this part starts with "no", and the
    word that takes away is followed only by "it" or "this"."""
    if not re.match(r"\s*yes\b", plain, re.I) or not re.match(r"\s*no\b", part, re.I):
        return False
    m = _REMOVING_RX.search(part)
    after = re.findall(r"\w+", part[m.end():]) if m else ["x"]
    return bool(after) and all(w.casefold() in ("it", "this") for w in after)


def _plain_things(words: list) -> bool:
    """True when every word is an ordinary word for a thing that could never
    be a person's name ("Dessert", "Kids"), by the shipped en_US word list
    (``catalogs.personal_words.is_name_like``; fails closed: a word the list
    does not know, or knows as a name too, "Leo", "Sam", "Rose", is not)."""
    try:
        from puffpai.catalogs import personal_words
    except Exception:  # noqa: BLE001 - no list: fail closed
        return False
    return bool(words) and all(w.isalpha() and not personal_words.is_name_like(w) for w in words)


def _second_part(plain: str) -> bool:
    """Final fix (hostile labels): a label split by a written mark into
    parts, where a part after the first asks for a step of its own. Such a
    part is refused when it takes something away; when it has two or more
    words and hands something out, changes something or changes who may do
    what; when it holds a word that joins one more step ("next", "ditto",
    "btw"); or when it names someone (a capitalized word in a text that is
    not title case). Fails closed: "Ship now, pay later" is the one shape of
    two changes allowed."""
    parts = [p for p in _PART_BREAK.split(plain) if p and p.strip()]
    if len(parts) < 2:
        return False
    if _when_parts(parts):
        return False
    all_words = re.findall(r"\w+", plain)
    names = _capital_names(all_words)
    for p in parts[1:]:
        ws = re.findall(r"\w+", p)
        if not ws:
            continue
        if _REMOVING_RX.search(p) and not _yes_no_hint(plain, p):
            return True
        if len(ws) >= 2 and (_SEND_WORD_RX.search(p) or _CHANGE_RX.search(p)
                             or _POWER_RX.search(p) or _DONE_RX.search(p)):
            return True
        if any(w.casefold() in _LATER_PART_JOIN for w in ws):
            return True
        if names and any(_name_cap(w) for w in ws[1:]):
            return True
    return False


def _extra_join(plain: str) -> bool:
    """Final fix: joiners the older rules let through."""
    words = re.findall(r"\w+", plain)
    if _second_part(plain):
        return True
    # "When paid delete Sam": a leading condition, then a change
    lead = _LEADING_WHEN.match(plain)
    if lead:
        rest = plain[lead.end():]
        if _REMOVING_RX.search(rest) or _SEND_WORD_RX.search(rest) or _CHANGE_RX.search(rest) \
                or _POWER_RX.search(rest):
            return True
    # "Then then", "Also also": one joiner twice
    if re.search(r"\b(then|also|and|too|next|plus)\W+\1\b", plain, re.I):
        return True
    # "Paid then": a change told as done, then "then" at the very end
    if re.search(r"\bthen\W*$", plain, re.I) and any(
            w.casefold() in _DONE_WORDS for w in words[:-1]):
        return True
    # "Paid & ping Leo", "Paid & also Leo": a joiner, then a name, in a text
    # that is not title case
    if _capital_names(words) and re.search(
            r"(?:(?<!\w)(?:&|\+)|\b(?:and|plus|with)\b)\W+(?:\w+\W+){0,2}?[A-Z]", plain):
        return True
    # "Paid plus Leo": a small-letter "plus" before a name
    if re.search(r"\bplus\W+[A-Z]", plain):
        return True
    return False


#: A thing's own name that opens with a word for later
#: ("Afterwards drinks", "Follow up call", "After party"): that word, then one
#: or two plain small-letter words for a thing, and nothing else.
_LATER_THING = re.compile(r"^\s*(?i:afterwards?|after|follow(?:ing)?[\s-]*up)\s+([a-z]+)(?:\s+([a-z]+))?\s*$")
_ONE_STEP_JOINERS = frozenset({"also", "then", "too", "plus", "next", "later", "and", "again",
                               "besides", "ditto", "likewise", "finally", "lastly"})


def _later_thing(plain: str) -> bool:
    """True for a thing's name like "Afterwards drinks" or "Follow up call":
    no change, removal, power, done, "many" or joining word, and no word that
    could be a name (:func:`_plain_things`). A word that hands something out
    may only be the thing itself, as the last word ("Follow up call")."""
    m = _LATER_THING.match(plain)
    if not m:
        return False
    words = [w for w in m.groups() if w]
    for i, w in enumerate(words):
        f = w.casefold()
        if f in _DONE_WORDS or f in _LATER_PART_JOIN or f in _ONE_STEP_JOINERS \
                or _ANY_CHANGE_RX.fullmatch(w) or _POWER_RX.fullmatch(w) or _MANY_RX.fullmatch(w):
            return False
        if _SEND_WORD_RX.fullmatch(w) and i != len(words) - 1:
            return False
    return _plain_things(words)


def _one_more_change(form: str) -> bool:
    """True when a text asks for one more change on top of the one asked for:
    "also", "then", "as well" and the like beside a word that takes something
    away, hands something out to someone, or changes who may do what ("Also
    remove Leo", "Then send it to Sam", "Also make Leo admin"), or two words
    that take something away ("Remove guest and remove Leo"); an "also"-like
    word with any change word ("Also set Leo to declined", "Also invite
    Eve"); a "then"-like word or "and" followed by a change word ("Then edit
    every row", "Pick this and remove Leo"), or one at the very start or end
    of a text that holds a change word ("Decline Leo later")."""
    plain = _ABILITY.sub(" ", form)
    # "Add a plus one": a plus one is a guest, not one more step
    plain = re.sub(r"\bplus[\s-]+(?:one|two|three|four|\d)\s*$", "guest", plain, flags=re.I)
    if _later_thing(plain):
        return False
    # joiners that need no change word
    if _ALWAYS_JOIN.search(plain):
        return True
    if _extra_join(plain):
        return True
    # "Also available in blue": a described option, read without the "also"
    # when nothing in it changes, hands out or names anyone
    if _ALSO_DESCRIBES.match(plain):
        rest = re.sub(r"^\s*also\s+", "", plain, flags=re.I)
        if not (_ANY_CHANGE_RX.search(rest) or _SEND_WORD_RX.search(rest)
                or _POWER_RX.search(rest) or _capital_names(re.findall(r"\w+", plain))
                or _ALSO.search(rest) or _THEN.search(rest)):
            plain = rest
    for m in _MARKED_STEP.finditer(plain):
        rest = re.findall(r"\w+", plain[m.start(1):])
        if not all(w.casefold() in _COURTESY for w in rest):
            return True
    # joiners told by where they stand, not by a change word
    if _STEP_ANYWHERE.search(plain) or _MID_PLUS.search(plain):
        return True
    for m in _MID_THEN.finditer(plain):
        if not _one_step(re.findall(r"\w+", plain[m.end():])):
            return True
    words = re.findall(r"\w+", plain)
    if words and words[0].casefold() == "also" and not _one_step(words[1:]):
        return True
    tail = _TRAILING_ALSO.search(plain)
    if tail and not _one_step(re.findall(r"\w+", plain[:tail.start()])):
        head = re.findall(r"\w+", plain[:tail.start()])
        # "Dessert too": one or two plain words for a thing (no change, no
        # hand-out, no done word, no joiner, no name) is an option, not a step
        thing = 1 <= len(head) <= 2 and not any(
            w.casefold() in _DONE_WORDS or w.casefold() in _LATER_PART_JOIN
            or _ANY_CHANGE_RX.fullmatch(w) or _SEND_WORD_RX.fullmatch(w)
            or _POWER_RX.fullmatch(w) for w in head) and _plain_things(head)
        if not thing:
            return True
    if _ALSO.search(plain) and _THEN_NOT_AND.search(plain):
        return True
    # "And remove Leo", "& refund Sam": a text that starts with "and" adds
    # to something, whatever follows
    if re.match(r"\s*(?:and|&|\+)\s+\w", plain, re.I):
        return True
    if len(_REMOVING_RX.findall(plain)) >= 2:
        return True
    if _ONE_MORE.search(plain) and (_REMOVING_RX.search(plain) or _SEND_OUT_RX.search(plain)
                                    or _POWER_RX.search(plain)):
        return True
    # a word that takes something away, and "and" or "plus" joining more to
    # it ("Remove Leo and Sam"): more than the one record an action changes
    if _REMOVING_RX.search(plain) and re.search(r"(?<!\w)(?:and|&|\+|plus)(?!\w)", plain, re.I):
        return True
    # "Sam gets declined as well": a change told as done, with "also"
    if _ALSO.search(plain) and _DONE_RX.search(plain):
        return True
    # a word that hands things out, with no "to" ("Then also export all
    # rows", "Additionally email every guest", "Mark paid, then text
    # everyone"): after a "then"-like word, or beside an "also"-like word
    # with a word for many ("Also send a copy" stays one step)
    if _SEND_WORD_RX.search(plain):
        if _ALSO.search(plain) and _MANY_RX.search(plain):
            return True
        for m in _THEN.finditer(plain):
            after = plain[m.end():]
            if not _SEND_WORD_RX.search(after):
                continue
            # a bare "and" joins names as often as steps ("Name and email"):
            # one more step only with a word for many ("and text everyone")
            if m.group(0).strip().casefold() not in ("and", "&", "+") or _MANY_RX.search(after):
                return True
    if not _ANY_CHANGE_RX.search(plain):
        return False
    if _ALSO.search(plain):
        return True
    text = plain.strip()
    # "Mark Leo declined, and Sam": one more person after a comma and "and"
    if re.search(r",\s*(?:and|&|\+|plus)\s+\w", text, re.I):
        return True
    parts = re.split(r"[,;]", text)
    if 1 < len(parts) <= 2 and _when_parts(parts) and all(
            len(p.split()) >= 2 for p in parts):
        return False                  # "Buy now, pay later", "Order now, pick up later"
    for m in _THEN.finditer(text):
        before = text[:m.start()].split()
        if m.group(0).strip().casefold() in ("and", "&", "+") and len(before) == 1 \
                and before[0].casefold() in _READ_FIRST:
            continue
        if _ANY_CHANGE_RX.search(text[m.end():]):
            return True
        at_edge = m.start() == 0 or not text[m.end():].strip(" ,.:-")
        # a change word alone (or with "me", "us", "it") before a "then"-like
        # word at the very end names no one else: an ordinary option ("Pay
        # later", "Pay me later"); "Remove Leo later" names Leo
        if at_edge and m.start() > 0 and len(before) >= 1 \
                and not _REMOVING_RX.search(before[0]) \
                and all(w.casefold() in _SELF_OR_WHEN for w in before[1:]):
            continue
        if at_edge and m.group(0).strip().casefold() not in ("and", "&", "+"):
            return True
    # "And remove Leo": a text that starts with "and" adds to something
    return bool(re.match(r"(?:and|&|\+)\s", text, re.I))


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def recipe_sha(recipe: dict) -> str:
    """Fingerprint of what the recipe does (its provenance and verdict left out)."""
    body = {k: v for k, v in recipe.items() if k not in ("provenance", "checked")}
    return hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


def app_sha(root: str) -> str:
    """Fingerprint of the app's files (names and bytes, links never followed)."""
    h = hashlib.sha256()
    from puffpai import webapps
    for dirpath, dirnames, files in webapps.walk(root):
        dirnames.sort()
        for fn in sorted(files):
            p = os.path.join(dirpath, fn)
            if os.path.islink(p) or not os.path.isfile(p):
                continue
            h.update(os.path.relpath(p, root).replace("\\", "/").encode("utf-8"))
            h.update(b"\0")
            with open(p, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            h.update(b"\0")
    return h.hexdigest()


def stricter(a: str, b: str) -> str:
    return a if EFFECT_ORDER.get(a, 2) >= EFFECT_ORDER.get(b, 2) else b


# ---------------------------------------------------------------------------
# R1-R9 and R12: rules that read the recipe only
# ---------------------------------------------------------------------------

def _row(rows: list, rule: str, ok: bool, why: str = "") -> None:
    rows.append({"rule": rule, "result": "pass" if ok else "fail", "why": why[:300]})


def selectors_of(recipe: dict) -> list[str]:
    out = []
    for s in recipe.get("steps") or []:
        if isinstance(s, dict):
            for k in ("sel", "row", "key"):
                v = s.get(k)
                if isinstance(v, str) and (k != "key" or s.get("do") != "read_storage"):
                    out.append(v)
    return out


def static_rules(recipe: Any, own_keys: set[str]) -> list[dict]:
    rows: list[dict] = []
    if not isinstance(recipe, dict):
        _row(rows, "R1", False, "not a recipe")
        return rows
    name = str(recipe.get("name") or "")
    says = str(recipe.get("says") or "")
    effect = recipe.get("effect")
    args = recipe.get("args") if isinstance(recipe.get("args"), dict) else {}
    steps = recipe.get("steps") if isinstance(recipe.get("steps"), list) else []
    _row(rows, "R1", bool(re.fullmatch(r"[a-z][a-z0-9_]{2,40}", name)) and 0 < len(says) <= 120
         and effect in EFFECT_ORDER and len(args) <= 12 and 0 < len(steps) <= 40
         and all(isinstance(s, dict) for s in steps),
         f"name={name!r} effect={effect} steps={len(steps)}")
    bad_verbs = [str(s.get("do")) for s in steps if isinstance(s, dict) and s.get("do") not in VERBS]
    bad_keys = [str(s.get("key")) for s in steps if isinstance(s, dict) and s.get("do") == "key"
                and s.get("key") not in KEYS_ALLOWED]
    _row(rows, "R2", not bad_verbs and not bad_keys,
         f"unknown steps {bad_verbs} {bad_keys}" if bad_verbs or bad_keys else "")
    bad_sel = []
    for s in steps:
        if not isinstance(s, dict):
            continue
        for k in ("sel", "row", "key", "cells"):
            v = s.get(k)
            if v is None or (k == "key" and s.get("do") in ("read_storage", "key")):
                continue
            if not isinstance(v, str) or not SAFE_SEL.fullmatch(v) or BAD_SEL.search(v):
                bad_sel.append(f"{s.get('do')}.{k}")
    _row(rows, "R3", not bad_sel, "; ".join(bad_sel))
    problems = []
    for s in steps:
        if not isinstance(s, dict):
            continue
        for k in ("arg", "match_arg", "if_arg"):
            a = s.get(k)
            if a is not None and a not in args:
                problems.append(f"step uses undeclared {a}")
        for k in ("sel", "row", "key", "text", "cells", "path"):
            v = s.get(k)
            if isinstance(v, str) and re.search(r"\{|\$\w", v):
                problems.append(f"{k} looks templated")
    for a, spec in args.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,40}", str(a)) or not isinstance(spec, dict):
            problems.append(f"{a}: bad argument")
            continue
        t = spec.get("type")
        if t == "number":
            if not isinstance(spec.get("min"), (int, float)) or not isinstance(spec.get("max"), (int, float)):
                problems.append(f"{a}: number without bounds")
        elif t == "string":
            ml = spec.get("maxLength")
            if not (spec.get("enum") or spec.get("pattern")
                    or (isinstance(ml, int) and 0 < ml <= 500)):
                problems.append(f"{a}: free text without a limit")
        else:
            problems.append(f"{a}: unknown type {t}")
    _row(rows, "R4", not problems, "; ".join(problems))
    check = recipe.get("check")
    has_check = isinstance(check, dict) and bool(check.get("storage_key"))
    writes = [s.get("do") for s in steps if isinstance(s, dict) and s.get("do") in WRITES]
    if effect in ("changes", "removes"):
        _row(rows, "R5", has_check, "" if has_check else "a change with no check of its result")
    else:
        _row(rows, "R5", not writes, f"a reading action that writes: {writes}" if writes else "")
    dialog = any(isinstance(s, dict) and s.get("do") == "accept_next_dialog" for s in steps)
    _row(rows, "R6", (not dialog) or effect == "removes",
         "answers the app's are-you-sure box but is not marked as removing" if dialog and effect != "removes" else "")
    keys = {s.get("key") for s in steps if isinstance(s, dict) and s.get("do") == "read_storage"}
    if has_check:
        keys.add(check.get("storage_key"))
    foreign = sorted(str(k) for k in keys if k and k not in own_keys)
    _row(rows, "R7", not foreign, f"reads storage the app never uses: {foreign}" if foreign else "")
    bad_words = [] if label_ok(says, "words") else ["the action's words"]
    for a, v in args.items():
        if not isinstance(v, dict):
            continue
        if not label_ok(str(a), "name"):
            bad_words.append(f"{a}: its name")
        if v.get("says") is not None and not label_ok(v.get("says"), "words"):
            bad_words.append(f"{a}: its words")
        choices = v.get("enum")
        if choices is not None:
            if not isinstance(choices, list) or not choices or len(choices) > MAX_CHOICES:
                bad_words.append(f"{a}: its list of choices")
            else:
                bad = [c for c in choices if not label_ok(c, "choice")]
                if bad:
                    bad_words.append(f"{a}: {len(bad)} of its choices")
    _row(rows, "R8", not bad_words,
         "words that read like orders, are too long, or hold banned words: " + "; ".join(bad_words[:6])
         if bad_words else "")
    files_ok = True
    for s in steps:
        if isinstance(s, dict) and s.get("do") == "set_files":
            files_ok = files_ok and _bare_file_pattern(
                str((args.get(s.get("arg")) or {}).get("pattern") or ""))
    _row(rows, "R9", files_ok, "" if files_ok else "a file argument that could name a folder")
    return rows


#: Names a file argument must never accept: each could reach outside the folder.
_FOLDERISH = ("../x", "..", ".", "a/b", "a\\b", "C:x", "/x", "\\\\h\\s", "x/", "~/x", "x\x00")


def _bare_file_pattern(pat: str) -> bool:
    """R9: the pattern is anchored and accepts no name that could reach a folder."""
    if not pat or not pat.startswith("^") or not pat.endswith("$") or len(pat) > 200:
        return False
    try:
        rx = re.compile(pat)
    except re.error:
        return False
    return not any(rx.fullmatch(p) for p in _FOLDERISH)


def provenance_row(recipe: Any, current_app_sha: str) -> dict:
    rows: list[dict] = []
    prov = recipe.get("provenance") if isinstance(recipe, dict) else None
    ok = (isinstance(prov, dict) and prov.get("by") in PROVENANCE_BY
          and prov.get("compiler_version") == COMPILER_VERSION
          and isinstance(current_app_sha, str) and len(current_app_sha) == 64
          and prov.get("app_sha") == current_app_sha
          and prov.get("recipe_sha") == recipe_sha(recipe))
    why = ""
    if not ok:
        if not isinstance(prov, dict) or prov.get("by") not in PROVENANCE_BY:
            why = "not made by Toolsmith's compiler"
        elif prov.get("compiler_version") != COMPILER_VERSION:
            why = "made by another version of the compiler"
        elif prov.get("app_sha") != current_app_sha:
            why = "made for other app files"
        else:
            why = "changed after it was made"
    _row(rows, "R12", ok, why)
    return rows[0]


# ---------------------------------------------------------------------------
# evidence (R10) and the effect proof (R11)
# ---------------------------------------------------------------------------

def pick(obj: Any, path: str) -> Any:
    cur = obj
    for part in (path.split(".") if path else []):
        if cur is None:
            return None
        if part == "*":
            cur = cur if isinstance(cur, list) else list(cur.values()) if isinstance(cur, dict) else None
            continue
        if isinstance(cur, list):
            nxt = []
            for x in cur:
                if isinstance(x, dict) and part in x:
                    v = x[part]
                    nxt.extend(v if isinstance(v, list) else [v])
            cur = nxt
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, (int, float)) and not isinstance(a, bool):
        try:
            return abs(float(a) - float(b)) < 0.005
        except (TypeError, ValueError):
            return False
    return str(a if a is not None else "").strip().lower() == str(b if b is not None else "").strip().lower()


def _loads(raw: Any) -> tuple[bool, Any]:
    if raw is None:
        return True, None
    try:
        return True, json.loads(raw)
    except (TypeError, ValueError):
        return False, raw


def evidence(recipe: dict, args: dict, before: dict, after: dict) -> tuple[bool, dict]:
    """Did the change land? (evidence or silence)."""
    check = recipe.get("check") or {}
    key = check.get("storage_key")
    if not key:
        return recipe.get("effect") == "reads", {}
    if check.get("value") is True:
        # a button that changes one stored number or yes/no (a counter, a
        # switch): it landed when that value is a new number or yes/no
        changed = before.get(key) != after.get(key) and _counter_value(after.get(key))
        return bool(changed), {"checked": key, "value_changed": bool(changed)}
    ok, data = _loads(after.get(key))
    items = pick(data, check.get("list") or "") if ok else None
    items = items if isinstance(items, list) else ([items] if isinstance(items, dict) else [])
    match = check.get("match") or {}
    expect = check.get("expect") or {}

    def val(a: str) -> Any:
        return args.get(a, ((recipe.get("args") or {}).get(a) or {}).get("default"))

    hits = [it for it in items if isinstance(it, dict)
            and all(_same(it.get(f), val(a)) for f, a in match.items())]
    good = [it for it in hits if all(_same(it.get(f), val(a)) for f, a in expect.items()
                                     if a in args)]
    # a tick box keeps yes or no as true or false
    truth = check.get("expect_truth") or {}
    if isinstance(truth, dict) and truth:
        good = [it for it in good if all(
            bool(it.get(f)) == (str(val(a)).strip().lower() in ("yes", "true", "on", "1"))
            for f, a in truth.items() if a in args)]
    # a button that flips a field: it landed only if that record really changed
    if check.get("changed") is True:
        ok_b, bdata = _loads(before.get(key))
        olds = pick(bdata, check.get("list") or "") if ok_b else None
        olds = olds if isinstance(olds, list) else []
        was = [it for it in olds if isinstance(it, dict)
               and all(_same(it.get(f), val(a)) for f, a in match.items())]
        good = [it for it in good if len(was) == 1 and canonical(it) != canonical(was[0])]
    want_present = check.get("present", True) is not False
    ev = {"checked": key, "matching_items": len(hits), "as_expected": len(good),
          "want": "present" if want_present else "absent"}
    return (len(good) > 0) == want_present, ev


def targets_a_row(recipe: dict) -> bool:
    """True when a step picks one existing row by an argument (a remove, a
    button flip, an edit of the row whose name the owner gave)."""
    return any(isinstance(s, dict) and s.get("match_arg") for s in recipe.get("steps") or [])


def matching_records(recipe: dict, args: dict, data: dict) -> int:
    """How many stored records the recipe's check matches for these arguments
    (0 when it has no check or no match)."""
    check = recipe.get("check") or {}
    key, match = check.get("storage_key"), check.get("match") or {}
    if not key or not isinstance(match, dict) or not match:
        return 0
    ok, value = _loads((data or {}).get(key))
    items = pick(value, check.get("list") or "") if ok else None
    items = items if isinstance(items, list) else ([items] if isinstance(items, dict) else [])
    named = _names_it(recipe, args)
    return sum(1 for it in items if named(it))


def _names_it(recipe: dict, args: dict) -> Callable[[Any], bool]:
    """True for a record the check's match fields name with these arguments."""
    match = (recipe.get("check") or {}).get("match") or {}

    def val(a: str) -> Any:
        return args.get(a, ((recipe.get("args") or {}).get(a) or {}).get("default"))

    return lambda it: isinstance(it, dict) and bool(match) and all(
        _same(it.get(f), val(a)) for f, a in match.items())


def _list_and_rest(raw: Any, path: str) -> Optional[tuple[list, Any]]:
    """(the checked list, the stored document with that list left out), or
    None when the list cannot be read through plain objects."""
    ok, doc = _loads(raw)
    if not ok or doc is None or "*" in (path or ""):
        return None
    if not path:
        return (doc, None) if isinstance(doc, list) else None
    rest = json.loads(json.dumps(doc))
    parts = path.split(".")
    cur = rest
    for p in parts[:-1]:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    if not isinstance(cur, dict) or not isinstance(cur.get(parts[-1]), list):
        return None
    return cur.pop(parts[-1]), rest


def one_row_only(recipe: dict, args: dict, before: dict, after: dict) -> bool:
    """For an action that picks one existing row by the owner's words: True
    only when the stored data changed in that one record and nothing else.

    Exactly one record matched before. No other stored key and nothing beside
    the list changed. A remove (``present: False``) took away that one record
    and every other record is still there, unchanged; a change left the list
    the same length with every other record unchanged. Counted at run time on
    the real data, because the app's code is not trusted to do only what the
    compiler saw it do."""
    from collections import Counter
    check = recipe.get("check") or {}
    key = check.get("storage_key")
    if not key or not isinstance(check.get("match"), dict) or not check.get("match"):
        return False
    before, after = before or {}, after or {}
    if any(before.get(k) != after.get(k) for k in set(before) | set(after) if k != key):
        return False
    path = str(check.get("list") or "")
    b = _list_and_rest(before.get(key), path)
    if b is None:
        return False
    bl, brest = b
    named = _names_it(recipe, args)
    targets = [x for x in bl if named(x)]
    if len(targets) != 1:
        return False
    removes = check.get("present", True) is False
    a = _list_and_rest(after.get(key), path)
    if a is None:
        # removing the only record may take the whole stored value with it,
        # but only when nothing else was kept beside the list
        _ok, adoc = _loads(after.get(key))
        return removes and len(bl) == 1 and brest in (None, {}) and adoc in (None, [], {})
    al, arest = a
    if canonical(brest) != canonical(arest):
        return False
    others = Counter(canonical(x) for x in bl)
    others[canonical(targets[0])] -= 1
    others = +others
    now = Counter(canonical(x) for x in al)
    if removes:
        return now == others
    return len(al) == len(bl) and not (others - now) and sum((now - others).values()) <= 1


class _Diff:
    def __init__(self) -> None:
        self.removed: list[str] = []
        self.replaced: list[str] = []
        self.added = 0
        self.edited: dict[str, Any] = {}


def _identity(*lists: list) -> Optional[str]:
    """An id field present in every record and unique within each list."""
    items = [x for lst in lists for x in lst]
    if not items or not all(isinstance(x, dict) for x in items):
        return None
    for f in _ID_FIELDS:
        ok = True
        for lst in lists:
            vals = [x.get(f) for x in lst]
            if not all(v is not None and not isinstance(v, (dict, list)) for v in vals) \
                    or len({canonical(v) for v in vals}) != len(vals):
                ok = False
                break
        if ok:
            return f
    return None


def _walk(b: Any, a: Any, path: str, rec: Optional[tuple[str, Any]], d: _Diff) -> None:
    if isinstance(b, dict) and isinstance(a, dict):
        for k in b:
            if k not in a:
                d.removed.append(f"{path}.{k}")
            else:
                _walk(b[k], a[k], f"{path}.{k}", rec, d)
        d.added += sum(1 for k in a if k not in b)
        return
    if isinstance(b, list) and isinstance(a, list):
        if len(a) < len(b):
            d.removed.append(path)
        idf = _identity(b, a)
        if idf is not None and all(isinstance(x, dict) for x in b + a):
            old = {canonical(x.get(idf)): x for x in b}
            new = {canonical(x.get(idf)): x for x in a}
            for k, x in old.items():
                if k not in new:
                    d.removed.append(f"{path}[{k}]")
                else:
                    _walk(x, new[k], f"{path}[{k}]", (f"{path}[{k}]", new[k]), d)
            d.added += sum(1 for k in new if k not in old)
            return
        before_items = [canonical(x) for x in b]
        after_items = [canonical(x) for x in a]
        pool = list(after_items)
        for x in before_items:
            if x in pool:
                pool.remove(x)
            else:
                d.removed.append(f"{path}[?]")
        d.added += len(pool)
        return
    if type(b) is not type(a) and not (isinstance(b, (int, float)) and isinstance(a, (int, float))
                                       and not isinstance(a, bool) and not isinstance(b, bool)):
        if isinstance(b, (dict, list)) or isinstance(a, (dict, list)):
            d.replaced.append(path)
            return
    if b != a:
        if rec is not None:
            d.edited[rec[0]] = rec[1]
        else:
            d.edited[f"root:{path}"] = None


def diff_storage(before: dict, after: dict) -> _Diff:
    d = _Diff()
    for key in before:
        if key not in after:
            d.removed.append(f"key:{key}")
    for key, raw in after.items():
        if key not in before:
            d.added += 1
            continue
        if raw == before[key]:
            continue
        ok_b, jb = _loads(before[key])
        ok_a, ja = _loads(raw)
        if not (ok_b and ok_a) or type(jb) is not type(ja):
            d.replaced.append(f"key:{key}")
            continue
        _walk(jb, ja, f"key:{key}", None, d)
    return d


def prove_effect(recipe: dict, args: dict, before: dict, after: dict, *,
                 clicked: list[str] = (), dialogs: list[dict] = ()) -> tuple[str, list[str]]:
    """The effect the run proved, and the reasons, from the stored-data diff."""
    value_change = _one_counter_change(recipe, before, after)
    d = diff_storage(before, after)
    reasons: list[str] = []
    proven = "reads"
    if d.removed:
        reasons.append("data was removed")
    if d.replaced:
        reasons.append("a whole value was replaced")
    match = (recipe.get("check") or {}).get("match") or {}
    edits = list(d.edited.items())
    if value_change:
        edits = []                    # its own one number or yes/no: a change, said below
    if len(edits) > 1:
        reasons.append("more than one record was edited")
    elif len(edits) == 1:
        where, rec = edits[0]
        matched = (isinstance(rec, dict) and bool(match)
                   and all(_same(rec.get(f), args.get(a)) for f, a in match.items()))
        if not matched:
            reasons.append("a record the details do not name was edited")
    words = [str(recipe.get("says") or "")]
    for s in recipe.get("steps") or []:
        if isinstance(s, dict) and isinstance(s.get("text"), str):
            words.append(s["text"])
    words += [str(c) for c in clicked]
    if any(DESTRUCTIVE.search(w) for w in words):
        reasons.append("a control says it deletes, clears or replaces")
    if any(isinstance(s, dict) and s.get("do") == "accept_next_dialog"
           for s in recipe.get("steps") or []) \
            or any(str((x or {}).get("answered")) == "yes" for x in dialogs):
        reasons.append("it answers the app's are-you-sure box")
    if reasons:
        proven = "removes"
    elif d.added or edits or value_change:
        proven = "changes"
    return proven, reasons


def _counter_value(raw: Any) -> bool:
    """True for stored text that is one number or one yes/no (a counter, a
    switch), never words, a list or a record."""
    ok, v = _loads(raw)
    return ok and (isinstance(v, bool) or (isinstance(v, (int, float)) and abs(v) < 1e12))


def _one_counter_change(recipe: Any, before: dict, after: dict) -> bool:
    """A button action's whole effect is one stored number or yes/no of its
    own changing (a row counter's "Increase row count"). True only
    when the recipe checks that one value (``check.value``), it has no
    arguments, exactly that key differs, it was a number or yes/no (or not
    there yet) and still is. Anything else (words, a list, a second key, a
    value taken away) is judged by the ordinary proof, which fails closed."""
    check = recipe.get("check") if isinstance(recipe, dict) else None
    if not isinstance(check, dict) or check.get("value") is not True or recipe.get("args"):
        return False
    key = check.get("storage_key")
    if not isinstance(before, dict) or not isinstance(after, dict) or not key:
        return False
    differ = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    if differ != {key} or key not in after:
        return False
    return (key not in before or _counter_value(before[key])) and _counter_value(after[key])


# ---------------------------------------------------------------------------
# the whole check
# ---------------------------------------------------------------------------

def _undoable(effect: str, keeps_mirror: bool) -> bool:
    return effect == "changes" and bool(keeps_mirror)


def check_recipe(recipe: dict, *, own_keys: set[str], current_app_sha: str,
                 driver: Any = None, new_copy: Optional[Callable[[], str]] = None,
                 test_args: Optional[dict] = None, keeps_mirror: bool = True,
                 seed: Optional[Callable[[str], None]] = None) -> dict:
    """One verdict. ``driver`` and ``new_copy`` run R10/R11 on a fresh copy.

    Without a driver the verdict is ``block`` (R10 cannot pass without a run).
    ``seed(label)`` may put data on the copy first (a read runs after an add).
    """
    rows = static_rules(recipe, own_keys)
    rows.append(provenance_row(recipe, current_app_sha))
    declared = recipe.get("effect") if isinstance(recipe, dict) else None
    effect = declared if declared in EFFECT_ORDER else "removes"
    proven = None
    run_info: dict[str, Any] = {}
    static_ok = all(r["result"] == "pass" for r in rows)
    if driver is None or new_copy is None:
        _row(rows, "selector_parses", False, "no copy to check on")
        _row(rows, "R10", False, "no test run")
        _row(rows, "R11", False, "no test run")
    elif not static_ok:
        _row(rows, "selector_parses", False, "not run: an earlier rule failed")
        _row(rows, "R10", False, "not run: an earlier rule failed")
        _row(rows, "R11", False, "not run: an earlier rule failed")
    else:
        label = new_copy()
        try:
            driver.open(label, fresh=True)
            if seed is not None:
                seed(label)
                driver.reload(label)
            bad = [s for s in dict.fromkeys(selectors_of(recipe)) if driver.count(label, s) is None]
            _row(rows, "selector_parses", not bad, f"does not parse: {bad}" if bad else "")
            if bad:
                _row(rows, "R10", False, "not run: a selector does not parse")
                _row(rows, "R11", False, "not run: a selector does not parse")
            else:
                check = recipe.get("check") if isinstance(recipe.get("check"), dict) else {}
                if declared == "changes" and check.get("present", True) is not False                         and check.get("match"):
                    # a first run on the empty copy, so the measured run sees what a
                    # later one does too (counters, dates the app keeps beside the list)
                    keys = set((check.get("match") or {}).values())
                    warm = {a: (f"{v}-w" if a in keys and isinstance(v, str) else v)
                            for a, v in (test_args or {}).items()}
                    driver.run(label, recipe, warm)
                before = driver.storage(label)["localStorage"]
                t0 = time.time()
                res = driver.run(label, recipe, dict(test_args or {}))
                after = driver.storage(label)["localStorage"]
                run_info = {"ms": int((time.time() - t0) * 1000), "error": res.get("error")}
                landed, ev = (evidence(recipe, test_args or {}, before, after)
                              if res.get("ok") else (False, {}))
                run_info["evidence"] = ev
                _row(rows, "R10", bool(res.get("ok")) and landed,
                     "" if res.get("ok") and landed else
                     ("the app did not take the change" if res.get("ok") else str(res.get("error") or "the run failed")))
                proven, reasons = prove_effect(recipe, test_args or {}, before, after,
                                               clicked=res.get("clicked") or [],
                                               dialogs=res.get("dialogs") or [])
                if declared == "reads" and proven != "reads":
                    _row(rows, "R11", False, "a reading action changed stored data")
                else:
                    _row(rows, "R11", True, "; ".join(reasons))
                effect = stricter(effect, proven)
        except Exception as exc:  # noqa: BLE001 - a check that breaks blocks
            rows = [r for r in rows if r["rule"] not in ("selector_parses", "R10", "R11")]
            _row(rows, "selector_parses", False, "the check copy failed")
            _row(rows, "R10", False, str(exc)[:200])
            _row(rows, "R11", False, "no test run")
        finally:
            try:
                driver.close(label)
            except Exception:  # noqa: BLE001
                pass
    verdict = "allow" if all(r["result"] == "pass" for r in rows) else "block"
    rows_sha = hashlib.sha256(canonical(rows).encode("utf-8")).hexdigest()
    return {"recipe": recipe.get("name") if isinstance(recipe, dict) else None,
            "verdict": verdict, "effect": effect, "effect_proven": proven,
            "undoable": _undoable(effect, keeps_mirror) and verdict == "allow",
            "rows": rows, "rows_sha": rows_sha, "run": run_info,
            "recipe_sha": recipe_sha(recipe) if isinstance(recipe, dict) else "",
            "app_sha": current_app_sha, "checker_version": CHECKER_VERSION,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


BLOCK_CODES = frozenset({"R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10", "R11",
                         "R12", "selector_parses"})
#: Every rule a stored ``allow`` must carry a passing row for.
REQUIRED_RULES = BLOCK_CODES


def static_effect_floor(recipe: Any) -> str:
    """The least strict effect the recipe's own steps and words allow.

    Read from the recipe alone (no run): answering the app's are-you-sure box
    or words that say delete, clear, replace and the like mean ``removes``;
    any step that types, chooses, clicks or presses a key means at least
    ``changes``; only a recipe with none of those can be ``reads``. A stored
    effect below this floor is never believed.
    """
    if not isinstance(recipe, dict):
        return "removes"
    steps = recipe.get("steps") if isinstance(recipe.get("steps"), list) else []
    words = [str(recipe.get("says") or "")]
    floor = "reads"
    for s in steps:
        if not isinstance(s, dict):
            return "removes"
        if s.get("do") == "accept_next_dialog":
            return "removes"
        if isinstance(s.get("text"), str):
            words.append(s["text"])
        if s.get("do") in WRITES:
            floor = "changes"
    if any(DESTRUCTIVE.search(w) for w in words):
        return "removes"
    return floor


def drawbridge_rows(draft: dict) -> list[dict]:
    """Rows for Drawbridge's ``web_recipe`` checker: the stored verdict's rows."""
    checked = (draft or {}).get("checked") or {}
    rows = checked.get("rows") if isinstance(checked.get("rows"), list) else None
    if rows is None:
        return [{"code": "R10", "result": "fail"}]
    return [{"code": r.get("rule"), "result": r.get("result")} for r in rows]


__all__ = ["static_rules", "provenance_row", "check_recipe", "prove_effect", "diff_storage",
           "evidence", "recipe_sha", "app_sha", "COMPILER_VERSION", "PROVENANCE_BY",
           "stricter", "drawbridge_rows", "BLOCK_CODES", "DESTRUCTIVE", "REQUIRED_RULES",
           "static_effect_floor", "label_ok", "LABEL_LIMITS", "MAX_CHOICES"]
