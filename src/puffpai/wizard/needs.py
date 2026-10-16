"""Finding out what a <kind> bot needs.

The goal sentence becomes a list of jobs the bot needs, every one tickable:

1. :func:`puffpai.catalogs.needs_rules.explicit_items` reads her own list at
   once ("you asked" jobs, in her words);
2. the bot's subject comes from ``personal_words`` or the same
   algorithm here; personal words are marked and never become search words;
3. the local model adds the usual jobs of this kind of bot
   (:mod:`puffpai.wizard.needs_llm`; the path is picked by the brain's size
   and measured speed), and fills in what each job does and search phrases;
4. merge: her items keep her words; a model job that is the same thing
   (reading-helper cosine >= 0.80) is folded into it; at most 10 jobs;
5. the written learn rule marks "learn about" jobs; knowledge parts of every
   other job are dropped.

Every search phrase carries its state from the outbound screen
(``security.outbound_query``): ``out`` (may leave after she presses
Start looking), ``held`` or ``blocked``. Without the screen in the build every
phrase is held and nothing leaves.

The result is ``needs/v1``. It holds no goal text; job names and ``does`` stay
on this computer and are never logged. :func:`sends` is the only way phrases
become something a searcher accepts.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
import re
import threading
import time
from typing import Any, Callable, Optional

from puffpai.catalogs import needs_rules
from puffpai.wizard import needs_llm

SCHEMA = "needs/v1"
MAX_JOBS = 10
MAX_IDS = 12
DEDUPE = 0.80

# -- owner-facing lines (also in tests/fixtures/ui_strings/needs_and_find_parts.txt) ----------
LINE_READING = "Reading what you wrote ..."
LINE_READ_DONE = "Reading what you wrote ... done"
LINE_FROM_WORDS = "From your words: {n} {things}"
LINE_ASKING = "Asking your bot's brain what {kinds} usually need ..."
LINE_ASKED = "Asking your bot's brain what {kinds} usually need ... {n} more"
LINE_SLOW = "Your bot's brain is slow on this computer, so BuildBot used your words and a short list."
LINE_FAILED = ("Your bot's brain did not answer in time, so this list comes from your own words. "
               "You can add more below.")
LINE_SMALL = ("Your bot's brain is too small to help with this, so this list comes from your own "
              "words. You can add more below.")
LINE_NO_BRAIN = ("Your bot's brain is not set up yet, so this list comes from your own words. "
                 "You can add more below.")
LINE_PRIVATE = "Kept private, never searched: {words}"
NOT_IN_BUILD = "not in this build yet"


def _optional(modname: str):
    try:
        return importlib.import_module(modname)
    except ModuleNotFoundError as e:
        if e.name is not None and (e.name == modname or modname.startswith(e.name + ".")):
            return None          # not in this build yet
        raise                    # exists but broken: fail loudly


#: goal digest -> (goal, home); process memory only, so choose()/sends() can
#: screen again without the goal ever being stored in the needs payload.
_GOALS: dict[str, tuple[str, Optional[dict]]] = {}
#: sha256(normalised goal + model + prompt) -> needs/v1, process memory only.
_CACHE: dict[str, dict] = {}
_LOCK = threading.Lock()


def _norm(goal: str) -> str:
    return " ".join(str(goal or "").split()).lower()


def goal_digest(goal: str) -> str:
    return "sha256:" + hashlib.sha256(_norm(goal).encode("utf-8")).hexdigest()


# -- the brain --------------------------------------------------------------------
def _get(cfg: Any, *names: str) -> Any:
    for name in names:
        if isinstance(cfg, dict):
            if cfg.get(name) not in (None, ""):
                return cfg.get(name)
        elif getattr(cfg, name, None) not in (None, ""):
            return getattr(cfg, name)
    return None


def _brain(cfg: Any) -> tuple[str, str]:
    """(Ollama address, model) from a wizard notebook dict or a PuffPAI config."""
    url = _get(cfg, "ollama_url", "url")
    model = _get(cfg, "chosen_model", "model")
    ollama = _get(cfg, "ollama")
    if ollama is not None:
        url = url or _get(ollama, "url")
        model = model or _get(ollama, "model")
    return str(url or "").strip(), str(model or "").strip()


def _home(cfg: Any) -> Optional[dict]:
    home = _get(cfg, "home", "household_place")
    return home if isinstance(home, dict) else None


def _speed_path() -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), "cache", "needs", "speed.json")


def _known_speed(cfg: Any, model: str) -> Optional[float]:
    given = _get(cfg, "brain_tok_s", "tok_s")
    if given is not None:
        try:
            return float(given)
        except (TypeError, ValueError):
            pass
    try:
        with open(_speed_path(), encoding="utf-8") as fh:
            value = json.load(fh).get(model)
        return float(value) if value is not None else None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def _remember_speed(model: str, tok_s: Optional[float]) -> None:
    if not model or not tok_s:
        return
    path = _speed_path()
    try:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            data = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            data = {}
        data[model] = tok_s
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def prewarm(cfg) -> None:
    """Load the brain while she types (an empty chat, kept 10 minutes). Never raises."""
    try:
        url, model = _brain(cfg)
        if not url or not model or needs_llm.choose_prompt(model, None) == "rules":
            return
        backend = needs_llm.make_backend(url, model, timeout=15, max_tokens=1)
        backend.prewarm("10m")
    except Exception:  # noqa: BLE001 - a warm-up that fails changes nothing
        return


# -- personal words and the screen ---------------------------------------------
def _really_personal(word: str) -> bool:
    """Is *word* a personal detail worth showing under "Kept private"?

    Relatives, dates, numbers, names and places are. Ordinary words that only
    say how she talks ("getting", "married", "opening", "kids", "two") are not
    shown there; they still never become search words."""
    w = str(word or "").lower()
    if w in needs_rules.RELATIVES or w in needs_rules.MONTHS or re.search(r"\d", w):
        return True
    if w in needs_rules.FILLER or w in needs_rules.LIFE_EVENTS or w in needs_rules.AUDIENCE \
            or w in needs_rules.PRONOUNS:
        return False
    return needs_rules.dict_kind(w) != "common"


#: Doing words that the doing-word list does not hold (said of the past).
_PAST_VERBS = frozenset("put puts got gets set sets made built bought gave took went".split())


def _personal(goal: str, home: Optional[dict]) -> dict:
    """hard words (goal order, no pronouns), held rows, subject, notes, marks."""
    pw_mod = _optional("puffpai.catalogs.personal_words")
    oq = _optional("puffpai.security.outbound_query")
    marks = needs_rules.personal_words(goal, home=home)
    if pw_mod is not None:
        pw = pw_mod.find(goal, home=home)
        hard = {str(w).lower() for w in pw.hard}
        for w in hard:
            if not marks.get(w, "").startswith("hard"):
                marks[w] = "hard:personal"
        held = [{"word": str(h.get("word") or ""), "why": str(h.get("why") or "name"),
                 "allowed": False} for h in pw.held if h.get("word")]
        # A word of her
        # own jobs that the screen searches ("drug" of "drug name
        # flashcards") is no personal word for the phrase builder, so no
        # phrase is built without it ("study name flashcard").
        job_words = set(getattr(pw, "subject_job", ()) or ())
        for h in held:
            if h["word"].lower() not in job_words:
                marks.setdefault(h["word"].lower(), "held:" + h["why"])
        subject, notes = str(pw.subject or ""), [str(n) for n in pw.subject_notes]
    else:
        hard = {w for w, why in marks.items() if why.startswith("hard:")}
        held = []
        for tok in needs_rules._tokens(goal):   # the words as she typed them, in order
            w = tok.lower()
            w = w[:-2] if w.endswith("'s") else w
            why = marks.get(w, "")
            if w in ("a", "an", "the") or w in needs_rules.FILLER:
                continue
            if why.startswith("held:") and not any(h["word"] == w for h in held):
                held.append({"word": w, "why": why.split(":", 1)[1].rstrip("?") or "name",
                             "allowed": False})
        subject, notes = needs_rules.subject_of(goal, home=home), []
    from puffpai.catalogs import goal_words
    private = [w for w in needs_rules.hard_words(goal, home=home)]
    covered = {goal_words.singular(w) for w in private} | set(private)
    for w in sorted(hard):
        if w not in covered and goal_words.singular(w) not in covered \
                and w not in needs_rules.PRONOUNS and _really_personal(w):
            private.append(w)
            covered |= {w, goal_words.singular(w)}
    # Every word held for good (a health or private detail, the time of her
    # plans: never ticked, never searched) is listed too, "engaged" of "my
    # brother just got engaged" as well as "brother".
    for h in (pw.held if pw_mod is not None else ()):
        w = str(h.get("word") or "").lower()
        if w and not h.get("can_allow") and h.get("why") in ("private", "health", "date") \
                and w not in covered and goal_words.singular(w) not in covered:
            private.append(w)
            covered |= {w, goal_words.singular(w)}
    # News about her life ("got engaged", "just moved") is hers too: it is
    # never searched, and the "Kept private" line says so (a wedding goal
    # lists "engaged" as well as "brother"). Display only.
    for tok in needs_rules._tokens(goal):
        w = tok.lower()
        if w in needs_rules.LIFE_EVENTS and w not in covered:
            private.append(w)
            covered.add(w)
    # One place per word. "Kept private, never searched" lists the words that
    # are held for good; the tick boxes list only words she may let out, never
    # one the line already names; numbers are not words to list.
    if pw_mod is not None:
        tickable = {str(h.get("word") or "").lower() for h in pw.held if h.get("can_allow")}
        held = [h for h in held if h["word"].lower() in tickable]
        ticks = {goal_words.singular(h["word"].lower()) for h in held} \
            | {h["word"].lower() for h in held}
        # A subject word is searched (with its plain note), so it is never
        # listed as kept private ("pregnant" for a pregnancy goal).
        sent = set(str(pw.subject or "").split()) | set(getattr(pw, "subject_also", ()) or ())
        private = [w for w in private if not re.search(r"\d", w)
                   and w not in ticks and goal_words.singular(w) not in ticks
                   and w not in sent and goal_words.singular(w) not in sent]
    # A subject with no word about what the bot is for ("bot be" from "a bot
    # to be her coach", "tutor" from "a tutor bot") is no subject: nothing is
    # searched for it and the brain's kind of bot is used instead.
    said = subject             # as she said it: "tutor" still makes a learn job
    subject = needs_rules.usable_subject(subject)
    words = subject.split()
    if any(marks.get(w, "").startswith("hard:") for w in words) \
            or any(w in needs_rules.VERBS or w in _PAST_VERBS
                   for w in words[:-1] if w not in ("budget", "plan")):
        # A doing word inside it ("dev put home", from "Dev put Home Assistant
        # on a box") says it is a piece of a sentence, not
        # what the bot is about: the brain's kind of bot is used instead.
        subject = ""
    sendable = False
    if oq is not None and subject:
        try:
            sendable = _subject_sendable(oq, goal, home, subject) is not None
        except Exception:  # noqa: BLE001 - a screen that fails sends nothing
            sendable = False
    return {"marks": marks, "held": held, "subject": subject, "said": said, "notes": notes,
            "private": private, "sendable": sendable}


def _subject_sendable(oq, goal: str, home: Optional[dict], subject: str):
    """The outbound screen's subject ``Sendable``, only when it is *subject*
    (the usable subject); None otherwise. Nothing else is ever minted here."""
    if not subject:
        return None
    subj = oq.subject_sendable(goal, home=home)
    if subj is None or not oq.is_sendable(subj) or subj.phrase != subject:
        return None
    return subj


#: Words a search phrase may not start or end with once a held word is gone.
_DANGLING = frozenset("""a an the and or of for to with in on at by from as into about my our
your his her their old new late next last first little big small early easy simple quick
own other""".split())


def _screen(phrases: list[str], *, goal: Optional[str], home: Optional[dict],
            allowed: list[str], hint: str, reduce: bool = True) -> list[dict]:
    """``[{"phrase","state","why"}]`` for one part's phrases."""
    oq = _optional("puffpai.security.outbound_query")
    if oq is None or goal is None:
        why = [NOT_IN_BUILD] if oq is None else ["not checked yet"]
        return [{"phrase": p, "state": "held", "why": why} for p in phrases]
    try:
        res = oq.screen(phrases, goal=goal, allowed_words=allowed, home=home, source_hint=hint)
    except Exception:  # noqa: BLE001
        return [{"phrase": p, "state": "held", "why": ["the check failed"]} for p in phrases]
    out_set = {s.phrase for s in res.sendable if oq.is_sendable(s)}
    held = {str(h.get("phrase")): [str(w.get("why")) for w in h.get("words") or []]
            for h in res.held}
    rows = []
    for p in phrases:
        norm = " ".join(re.sub(r"[^a-z0-9 ]", " ", p.lower()).split())
        if p in out_set or norm in out_set:
            rows.append({"phrase": p, "state": "out", "why": []})
        elif p in held:
            rows.append({"phrase": p, "state": "held", "why": held[p]})
        else:
            rows.append({"phrase": p, "state": "blocked", "why": ["shape"]})
    if reduce and rows and not any(r["state"] == "out" for r in rows):
        # Every phrase of this part holds a private word ("flashcards for drug
        # names"): the same phrases without the held words are checked once
        # more, so the job is still searched for by its ordinary words.
        shorter: list[str] = []
        for h in res.held:
            words = {str(w.get("word") or "").lower() for w in h.get("words") or []}
            # Never a fragment. A held word may only come off the
            # start or the end of the phrase ("study drug name flashcard"
            # never becomes "study name flashcard"), what is left has two
            # words or more, and it neither starts nor ends with a joining
            # word or a word that only describes ("to deals", "server old").
            all_words = str(h.get("phrase") or "").split()
            gone = [k for k, w in enumerate(all_words) if w.lower() in words]
            keep = [w for w in all_words if w.lower() not in words]
            edge = bool(gone) and (gone == list(range(len(gone))) or gone == list(
                range(len(all_words) - len(gone), len(all_words))))
            if not edge or len(keep) < 2 or keep[0].lower() in _DANGLING \
                    or keep[-1].lower() in _DANGLING:
                continue
            left = " ".join(keep)
            if left and left not in phrases and left not in shorter \
                    and not needs_rules.only_tool_words(left):
                # ("tinder date logger" less its private words is "logger":
                # a tool's name alone searches for nothing of hers; one word
                # left, "prep" or "tips", searches for anything)
                shorter.append(left)
        if shorter:
            rows += [r for r in _screen(shorter, goal=goal, home=home, allowed=allowed,
                                        hint=hint, reduce=False) if r["state"] == "out"]
    return rows


# -- jobs ---------------------------------------------------------------------------
_PICTURE_RE = re.compile(r"\b(picture|pictures|photo|photos|image|images)\b", re.I)


def _rule_parts(name: str, kind_words: list[str], marks: dict, learn: bool) -> list[dict]:
    phrases = needs_rules.rule_phrases(kind_words, name, personal=marks)
    parts = [{"kind": "code-app", "search": list(phrases)}]
    if _PICTURE_RE.search(name):
        parts.append({"kind": "pictures", "search": list(phrases)})
    if learn:
        parts.append({"kind": "knowledge", "search": list(phrases)})
    return parts


def _job(jid: str, name: str, does: str, source: str, parts: list[dict], subject: str) -> dict:
    learn = needs_rules.is_learn(name, does, subject)
    return {"id": jid, "name": name, "does": does, "source": source, "seen_in": [],
            "ticked": True, "learn": learn,
            "parts": [{"kind": p["kind"], "search": list(p.get("search") or [])} for p in parts]}


def _merge_parts(into: list[dict], extra: list[dict]) -> None:
    for part in extra:
        same = next((p for p in into if p["kind"] == part["kind"]), None)
        if same is None:
            into.append({"kind": part["kind"], "search": list(part.get("search") or [])})
        else:
            for s in part.get("search") or []:
                if s not in same["search"] and len(same["search"]) < 4:
                    same["search"].append(s)


def _sims(a: list[str], b: list[str]):
    """Cosine matrix (len(a) x len(b)) by meaning, or by shared words without the helper."""
    from puffpai.catalogs import meaning
    if not a or not b:
        return [[0.0] * len(b) for _ in a]
    if meaning.helper_state() == "ready":
        try:
            m = meaning.embed(a) @ meaning.embed(b).T
            return [[float(x) for x in row] for row in m]
        except Exception:  # noqa: BLE001 - fall back to words
            pass
    from puffpai.catalogs import goal_words

    def words(t: str) -> set:
        return set(goal_words.goal_terms(t, limit=40))
    wa, wb = [words(x) for x in a], [words(x) for x in b]
    return [[len(x & y) / max(1, len(x | y)) for y in wb] for x in wa]


def _plain_name(name: str, marks: dict) -> str:
    """A job name without pronouns and personal words, for comparing only."""
    return " ".join(w for w in needs_rules._tokens(name)
                    if w.lower() not in needs_rules.PRONOUNS
                    and not marks.get(w.lower(), "").startswith("hard"))


_SMALL = frozenset("a an the and or of for to in on with from by at as into our my your".split())


def _content(name: str, marks: dict) -> set[str]:
    words = set()
    for w in needs_rules._tokens(_plain_name(name, marks)):
        w = w.lower()
        if w in _SMALL or w in needs_rules.FILLER:
            continue
        words.add(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w)
    return words


def _inside(name: str, jobs: list[dict], marks: dict) -> Optional[dict]:
    """The first job whose own words hold every word of *name* (None: none does)."""
    words = _content(name, marks)
    if not words:
        return None
    for job in jobs:
        if words <= _content(job["name"], marks):
            return job
    return None


#: A brain job that restates one of her items joins the item it is this close to.
FOLD = 0.60


def _restatements(model: dict, m_name: list[str], kind: str) -> set[int]:
    """The brain's jobs that restate something she asked for (closer to an
    item of its own "asked" list than to any of its "also_needed" list)."""
    asked = [str(a) for a in model.get("asked") or [] if str(a).strip()]
    also = [str(a) for a in model.get("also_needed") or [] if str(a).strip()]
    if not asked or not also or not m_name:
        return set()
    pre = (kind + " ") if kind else ""
    s_asked = _sims(m_name, [(pre + a).strip() for a in asked])
    s_also = _sims(m_name, [(pre + a).strip() for a in also])
    return {i for i in range(len(m_name)) if max(s_asked[i]) > max(s_also[i])}


def _merge(rule_jobs: list[dict], model: dict, marks: dict) -> list[dict]:
    """Her items first (her words kept), model jobs folded in or added; at most 10.

    Comparisons use the model's own kind as the prefix on both sides and her
    item names without pronouns or personal words (measured on the 12 recorded
    goals: 51 of 55 listed items paired with the model's job for them).
    """
    kind = str(model.get("kind") or "")
    jobs = [dict(j) for j in rule_jobs]
    mjobs = model.get("jobs") or []
    n_asked = len(model.get("asked") or [])
    if not mjobs:
        return jobs[:MAX_JOBS]
    m_name = [f"{kind} {j['name']}".strip() for j in mjobs]
    m_full = [f"{kind} {j['name']}: {j['does']}".strip() if j.get("does") else m_name[i]
              for i, j in enumerate(mjobs)]
    r_text = [f"{kind} {_plain_name(j['name'], marks)}".strip() for j in jobs]
    paired: dict[int, int] = {}
    if jobs:
        s1, s2 = _sims(m_name, r_text), _sims(m_full, r_text)
        pairs = sorted(((max(s1[i][k], s2[i][k]), i, k) for i in range(len(mjobs))
                        for k in range(len(jobs))), reverse=True)
        used: set[int] = set()
        for sim, i, k in pairs:
            if sim < DEDUPE:
                break
            if i in paired or k in used:
                continue
            paired[i] = k
            used.add(k)
    for i, k in paired.items():
        j = jobs[k]
        if not j.get("does"):
            j["does"] = mjobs[i].get("does") or ""
        parts = [dict(p, search=list(p["search"])) for p in j["parts"]]
        # The model's phrases lead; the rule phrases stay as a backstop.
        model_parts = [dict(p, search=list(p["search"])) for p in mjobs[i]["parts"]]
        _merge_parts(model_parts, parts)
        j["parts"] = model_parts
    restates = _restatements(model, m_name, kind)
    added: list[tuple[int, dict]] = []
    for i, mj in enumerate(mjobs):
        if i in paired:
            continue
        if any(marks.get(w.lower(), "").startswith("hard")
               for w in needs_rules._tokens(mj["name"])):
            continue  # a job named after something personal ("son reminder")
        if i in restates and jobs:
            # Something she asked for, said in the brain's words ("NPC
            # generator" for her "comes up with NPCs and loot"): it joins her
            # item when it is part of it, else it is one of hers. Never a
            # "usual" job that takes finds away from what she asked
            # (invented jobs must not outrank asked ones).
            sims = _sims([m_full[i]], r_text)[0]
            k = max(range(len(jobs)), key=lambda x: sims[x])
            if sims[k] >= FOLD:
                jobs[k]["parts"] = [dict(p, search=list(p.get("search") or []))
                                    for p in jobs[k]["parts"]]
                _merge_parts(jobs[k]["parts"], mj["parts"])
                if not jobs[k].get("does"):
                    jobs[k]["does"] = mj.get("does") or ""
                continue
            if _inside(mj["name"], jobs + [a for _, a in added], marks) is None:
                added.append((i, {"name": mj["name"][:1].upper() + mj["name"][1:],
                                  "does": mj.get("does") or "", "source": "asked",
                                  "parts": mj["parts"]}))
                continue
        inside = _inside(mj["name"], jobs + [a for _, a in added], marks)
        if inside is not None:
            # "Metronome" next to her "Metronome and tuner": the same job said
            # shorter. Its searches join hers; it is not a second job.
            inside["parts"] = [dict(p, search=list(p.get("search") or []))
                               for p in inside["parts"]]
            _merge_parts(inside["parts"], mj["parts"])
            continue
        texts = [(f"{kind} {_plain_name(j['name'], marks)}: {j['does']}" if j.get("does")
                  else f"{kind} {_plain_name(j['name'], marks)}").strip()
                 for j in jobs + [a for _, a in added]]
        if texts and max(_sims([m_full[i]], texts)[0]) >= DEDUPE:
            continue
        added.append((i, {"name": mj["name"][:1].upper() + mj["name"][1:], "does": mj.get("does") or "",
                          "source": "asked" if i < n_asked and not jobs else "usual",
                          "parts": mj["parts"]}))
    for _i, job in added:
        if job["source"] == "usual":
            # The brain's own jobs in plain doing words ("Compare prices", not
            # "Price comparator"); her own items keep her words.
            job["name"] = needs_rules.plain_job_name(job["name"])
    out = jobs + [a for _, a in added]
    out.sort(key=lambda j: 0 if j["source"] == "asked" else 1)
    return out[:MAX_JOBS]


#: Gadgets, platforms and tools a search phrase may name only when her own
#: words do: the brain added "smartwatch notifications", "power strip",
#: "oven temperature" and "kiwix offline flashcards" to goals that never
#: said them, and each one searched for something
#: she did not ask for.
_GADGETS = re.compile(
    r"\b(?:smart ?watch(?:es)?|wristbands?|wearables?|fitness bands?|"
    r"smart ?phones?|phones?|mobile|iphone|ipad|android|ios|tablets?|alexa|google home|"
    r"echo dot|smart plugs?|power strips?|outlets?|plugs?|sockets?|ovens?|desks?|"
    r"kiwix|open source|"
    r"arduino|raspberry pi|esp32|excel|google sheets|trello|slack|discord|telegram|"
    r"whatsapp|chrome extension|browser extension)\b", re.I)


def _off_goal_gadgets(jobs: list[dict], goal: str) -> None:
    """Search phrases that name a gadget or a platform she never mentioned
    are dropped (:data:`_GADGETS`); a part left with none borrows its job's
    other phrases (:func:`_fill_parts`)."""
    said = {m.group(0).lower() for m in _GADGETS.finditer(str(goal or ""))}
    for job in jobs:
        for part in job.get("parts") or []:
            keep = []
            for p in part.get("search") or []:
                text = p.get("phrase") if isinstance(p, dict) else str(p)
                hits = {m.group(0).lower() for m in _GADGETS.finditer(str(text or ""))}
                if not hits - said:
                    keep.append(p)
            part["search"] = keep


def _fill_parts(jobs: list[dict], kind: str, marks: dict) -> None:
    """Every part of every job gets real search phrases (with none, a
    genealogy job's code part never asks GitHub for a family-tree app). A bare word gets the kind of bot in front
    ("reminder" -> "pregnancy reminder"); an empty part borrows its job's other
    phrases, then the job's own words with the kind, then the kind alone."""
    kw = [w for w in str(kind or "").split() if w]
    for job in jobs:
        if not job.get("parts"):
            job["parts"] = [{"kind": "code-app", "search": []}]
        parts = job["parts"]
        for part in parts:
            got = [str(x) for x in part.get("search") or [] if str(x).strip()]
            if kw:
                got = [f"{kind} {x}" if len(x.split()) == 1 and x not in kw else x for x in got]
            part["search"] = list(dict.fromkeys(got))
        for part in parts:
            if part["search"]:
                continue
            borrowed = [x for q in parts if q is not part for x in q["search"]
                        if q["kind"] not in ("device",)]
            borrowed = [re.sub(r"\s+(?:template|spreadsheet|sheet|log|form)$", "", x)
                        for x in borrowed]
            part["search"] = (list(dict.fromkeys(x for x in borrowed if x))[:3]
                              or needs_rules.rule_phrases(kw, job["name"], personal=marks)
                              or ([kind] if kind else []))


def _finish(jobs: list[dict], subject: str) -> list[dict]:
    """Ids, the learn rule and the knowledge-part rule (K2)."""
    out = []
    for n, j in enumerate(jobs, 1):
        job = _job(f"j{n}", j["name"], j.get("does") or "", j["source"], j["parts"], subject)
        if not job["learn"]:
            kept = [p for p in job["parts"] if p["kind"] != "knowledge"]
            if not kept and job["parts"]:
                # Only a knowledge part on a job that is not about learning
                # ("Driving tips"): it is searched as apps, never left empty.
                kept = [{"kind": "code-app",
                         "search": list(dict.fromkeys(s for p in job["parts"]
                                                      for s in p["search"]))[:4]}]
            job["parts"] = kept
        out.append(job)
    return out


def _screen_jobs(jobs: list[dict], goal: Optional[str], home: Optional[dict],
                 allowed: list[str]) -> None:
    for job in jobs:
        for part in job["parts"]:
            phrases = [s["phrase"] if isinstance(s, dict) else str(s) for s in part["search"]]
            part["search"] = _screen(phrases, goal=goal, home=home, allowed=allowed,
                                     hint=job["id"])


def _things(n: int) -> str:
    return "thing" if n == 1 else "things"


def _kinds(kind: str) -> str:
    return f"{kind} bots" if kind else "bots like this"


def _clean_kind(kind: str) -> str:
    words = re.sub(r"[^a-z0-9 &]", " ", str(kind or "").lower()).replace("&", "and").split()
    while words and words[-1] in ("bot", "bots", "chatbot", "helper", "assistant", "app"):
        words.pop()
    return " ".join(words[:5])


# -- the step -----------------------------------------------------------------------
def run(goal: str, *, cfg, on_line: Optional[Callable[[str], None]] = None,
        on_job: Optional[Callable[[dict], None]] = None, deadline_s: float = 60.0) -> dict:
    """The needs/v1 answer for *goal* (rules at once, then the brain).

    ``on_line`` gets the plain progress lines. ``on_job`` gets
    ``{"id", "name", "source"}``: her own items at once (with ids), each new
    model job as it streams in (``id`` None, ``"provisional": True``), then
    every model job of the merged list with its id. The returned needs/v1 is
    the one that counts.
    """
    say = on_line or (lambda _line: None)
    t0 = time.monotonic()
    home = _home(cfg)
    url, model = _brain(cfg)
    speed = _known_speed(cfg, model) if model else None
    prompt = needs_llm.choose_prompt(model, speed) if url and model else "rules"
    digest = goal_digest(goal)
    key = hashlib.sha256(f"{_norm(goal)}\n{model}\n{prompt}".encode("utf-8")).hexdigest()
    with _LOCK:
        _GOALS[digest] = (str(goal or ""), home)
        cached = _CACHE.get(key)
    if cached is not None:
        return copy.deepcopy(cached)

    say(LINE_READING)
    p = _personal(goal, home)
    subject = p["subject"]
    kind_words = subject.split()
    rules = needs_rules.rule_needs(goal, personal=p["marks"], kind=kind_words, home=home)
    rule_jobs = []
    learn_subject = subject or p["said"]
    for item in rules["jobs"]:
        learn = needs_rules.is_learn(item["name"], "", learn_subject)
        parts = [{"kind": "code-app", "search": list(item["search"])}]
        if _PICTURE_RE.search(item["name"]):
            parts.append({"kind": "pictures", "search": list(item["search"])})
        if learn:
            parts.append({"kind": "knowledge", "search": list(item["search"])})
        rule_jobs.append({"name": item["name"], "does": "", "source": "asked", "parts": parts})
    rules_ms = round((time.monotonic() - t0) * 1000, 1)
    say(LINE_READ_DONE)
    say(LINE_FROM_WORDS.format(n=len(rule_jobs), things=_things(len(rule_jobs))))
    for n, j in enumerate(rule_jobs, 1):
        if on_job is not None:
            on_job({"id": f"j{n}", "name": j["name"], "source": "asked"})

    kind, kind_source = subject, "rules"
    timings: dict = {"rules_ms": rules_ms, "first_job_s": None, "model_s": None}
    used_prompt = "rules"
    merged = rule_jobs
    if prompt == "rules":
        say(LINE_NO_BRAIN if not (url and model) else LINE_SMALL)
    else:
        if prompt == needs_llm.PROMPT_STAGE_A:
            say(LINE_SLOW)
        else:
            say(LINE_ASKING.format(kinds=_kinds(kind)))
        def _streamed(job: dict) -> None:
            # A job as its closing brace arrives: shown at once unless it is one
            # of her own items or named after something personal. Provisional
            # (no id); the merged list below is the one that counts.
            if on_job is None or any(p["marks"].get(w.lower(), "").startswith("hard")
                                     for w in needs_rules._tokens(job["name"])):
                return
            mine = [f"{kind} {_plain_name(j['name'], p['marks'])}".strip() for j in rule_jobs]
            if mine and max(_sims([f"{kind} {job['name']}".strip()], mine)[0]) >= DEDUPE:
                return
            on_job({"id": None, "name": job["name"][:1].upper() + job["name"][1:],
                    "source": "usual", "provisional": True})

        try:
            left = max(1.0, deadline_s - (time.monotonic() - t0))
            answer = needs_llm.ask(goal, url=url, model=model, prompt=prompt, deadline_s=left,
                                   on_job=_streamed)
        except needs_llm.NeedsUnavailable:
            answer = None
            say(LINE_FAILED)
        if answer is not None:
            used_prompt = prompt
            _remember_speed(model, answer["timings"].get("tok_s"))
            timings["first_job_s"] = answer["timings"].get("first_job_s")
            timings["model_s"] = answer["timings"].get("model_s")
            if not kind:
                kind, kind_source = _clean_kind(answer.get("kind") or ""), "model"
            if prompt == needs_llm.PROMPT_STAGE_A:
                for j in answer["jobs"]:
                    j["parts"] = _rule_parts(j["name"], kind.split(), p["marks"],
                                             needs_rules.is_learn(j["name"], j["does"],
                                                                  subject or kind or p["said"]))
            merged = _merge(rule_jobs, answer, p["marks"])
            # Only the jobs most bots of this kind have: one of hers said in
            # the brain's words is not "more".
            more = sum(1 for j in merged if j.get("source") == "usual")
            if prompt == needs_llm.PROMPT_V5:
                say(LINE_ASKED.format(kinds=_kinds(kind), n=max(0, more)))
            if on_job is not None:
                for n, j in enumerate(merged[len(rule_jobs):], len(rule_jobs) + 1):
                    on_job({"id": f"j{n}", "name": j["name"], "source": j["source"]})
    if p["private"]:
        say(LINE_PRIVATE.format(words=", ".join(p["private"])))
    if used_prompt == "rules":
        # No brain answered, so nothing says which kinds of parts fit a job:
        # every kind that can be matched by meaning is looked at, and the
        # meaning check decides (a live card or a list only when it fits).
        for j in merged:
            phrases = next((list(pt["search"]) for pt in j["parts"] if pt["kind"] == "code-app"),
                           [])
            _merge_parts(j["parts"], [{"kind": "data-template", "search": phrases},
                                      {"kind": "live-info", "search": phrases}])

    _off_goal_gadgets(merged, goal)
    _fill_parts(merged, subject or kind, p["marks"])
    jobs = _finish(merged, " ".join(x for x in (subject or kind, p["said"]) if x))
    _screen_jobs(jobs, goal, home, [])
    from puffpai.catalogs import meaning
    result = {
        "schema": SCHEMA, "prompt": used_prompt, "model": model,
        "goal_digest": digest, "kind": kind, "kind_source": kind_source,
        "subject": {"phrase": subject, "notes": list(p["notes"]), "sendable": bool(p["sendable"])},
        "jobs": jobs, "held": p["held"], "private_left_out": p["private"],
        "helper": "ready" if meaning.helper_state() == "ready" else "missing",
        "timings": timings,
    }
    with _LOCK:
        _CACHE[key] = copy.deepcopy(result)
    return result


def from_words(words: str, *, cfg) -> dict:
    """needs/v1 for the Add page or chat "add ...": her words as one job, no model call."""
    home = _home(cfg)
    text = " ".join(str(words or "").split())
    p = _personal(text, home)
    subject = p["subject"]
    items = needs_rules.explicit_items(text) or [text]
    kind_words = subject.split() or [w for w in needs_rules.item_words(text, p["marks"])][:2]
    jobs = []
    for item in items[:MAX_JOBS]:
        name = needs_rules.job_name(item)
        if not name:
            continue
        learn = needs_rules.is_learn(name, "", subject or p["said"])
        phrases = needs_rules.rule_phrases(kind_words, name, personal=p["marks"]) \
            or [" ".join(needs_rules.item_words(name, p["marks"])[:3])]
        parts = [{"kind": "code-app", "search": [x for x in phrases if x]},
                 {"kind": "data-template", "search": [x for x in phrases if x]}]
        if learn:
            parts.append({"kind": "knowledge", "search": [x for x in phrases if x]})
        jobs.append({"name": name, "does": "", "source": "owner", "parts": parts})
    digest = goal_digest(text)
    with _LOCK:
        _GOALS[digest] = (text, home)
    out = _finish(jobs, subject or p["said"])
    _screen_jobs(out, text, home, [])
    from puffpai.catalogs import meaning
    return {"schema": SCHEMA, "prompt": "rules", "model": "", "goal_digest": digest,
            "kind": subject, "kind_source": "rules",
            "subject": {"phrase": subject, "notes": list(p["notes"]), "sendable": bool(p["sendable"])},
            "jobs": out, "held": p["held"], "private_left_out": p["private"],
            "helper": "ready" if meaning.helper_state() == "ready" else "missing",
            "timings": {"rules_ms": 0.0, "first_job_s": None, "model_s": None}}


def choose(needs: dict, *, ticked: list[str], added: list[str], allowed_words: list[str]) -> dict:
    """Her choices on the needs step: ticks, her own jobs, held words she allowed."""
    out = copy.deepcopy(needs)
    ticked_set = {str(t) for t in ticked or ()}
    for job in out.get("jobs") or []:
        job["ticked"] = job["id"] in ticked_set
    allowed = [str(w).lower() for w in allowed_words or () if str(w).strip()]
    for row in out.get("held") or []:
        row["allowed"] = str(row.get("word") or "").lower() in allowed
    goal, home = _GOALS.get(str(out.get("goal_digest") or ""), (None, None))
    subject = str((out.get("subject") or {}).get("phrase") or out.get("kind") or "")
    marks = needs_rules.personal_words(goal, home=home) if goal else {}
    jobs = out.setdefault("jobs", [])
    existing = {j["name"].lower() for j in jobs}
    for text in added or ():
        name = needs_rules.job_name(text)[:60]
        if not name or name.lower() in existing or len(jobs) >= MAX_IDS:
            continue
        existing.add(name.lower())
        used = {j["id"] for j in jobs}
        jid = next(f"j{n}" for n in range(1, MAX_IDS + 1) if f"j{n}" not in used)
        learn = needs_rules.is_learn(name, "", subject)
        parts = _rule_parts(name, subject.split(), marks, learn)
        job = _job(jid, name, "", "owner", parts, subject)
        job["ticked"] = True
        jobs.append(job)
    for job in jobs:
        if not job["learn"]:
            job["parts"] = [p for p in job["parts"] if p["kind"] != "knowledge"]
    _screen_jobs(jobs, goal, home, allowed)
    return out


HELPER_READY = "BuildBot's reading helper is ready."
HELPER_NOT_IN_BUILD = "Getting the reading helper is not in this build yet."
HELPER_NO_YES = "BuildBot did not get the reading helper: that yes did not come from this device."
HELPER_FAILED = "BuildBot could not get the reading helper, so it will match by words."
HELPER_GETTING = "Getting BuildBot's reading helper (83 MB, once) ..."


def get_helper(*, ticket: str, device_id: str, progress) -> dict:
    """Download and install the reading helper after the owner's yes.

    The yes is a consent ticket minted for ``add_on`` / ``reading_helper`` on
    this device; it is spent here. The archive comes only from the helper's
    own pinned address and must match its pinned check number. Returns
    ``{"state": "ready"|"failed", "line": plain words}``. Never raises."""
    say = progress if callable(progress) else (lambda _line: None)
    try:
        from puffpai.knowledge import reading_helper
    except Exception:  # noqa: BLE001
        return {"state": "failed", "line": HELPER_NOT_IN_BUILD}
    if meaning_ready():
        return {"state": "ready", "line": HELPER_READY}
    tickets = _optional("puffpai.security.consent_tickets")
    source_fetch = _optional("puffpai.source_fetch")
    if tickets is None or source_fetch is None \
            or not callable(getattr(source_fetch, "get_archive", None)):
        return {"state": "failed", "line": HELPER_NOT_IN_BUILD}
    try:
        ok, _verdict = tickets.redeem(ticket, device_id, "add_on", "reading_helper")
    except Exception:  # noqa: BLE001 - a check that fails is a no
        ok = False
    if not ok:
        return {"state": "failed", "line": HELPER_NO_YES}
    from urllib.parse import urlparse
    from puffpai.config import get_state_dir
    folder = os.path.join(get_state_dir(), "cache", "helper-download")
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, "reading-helper.tar.gz")
    say(HELPER_GETTING)
    try:
        got = source_fetch.get_archive(
            reading_helper.ARCHIVE_URL, allowed_hosts={urlparse(reading_helper.ARCHIVE_URL).hostname},
            max_bytes=int(getattr(reading_helper, "MAX_ARCHIVE_BYTES", 90 * 1024 * 1024)),
            dest_path=dest, service="reading_helper",
            expect_sha256=reading_helper.ARCHIVE_SHA256)
        path = str((got or {}).get("path") or dest)
        done = reading_helper.install_from_file(path)
    except Exception:  # noqa: BLE001 - a download that fails changes nothing
        done = {"ok": False}
    finally:
        try:
            if os.path.exists(dest):
                os.remove(dest)
        except OSError:
            pass
    if not done.get("ok"):
        return {"state": "failed", "line": HELPER_FAILED}
    return {"state": "ready", "line": HELPER_READY}


def meaning_ready() -> bool:
    from puffpai.catalogs import meaning
    return meaning.helper_state() == "ready"


def sends(needs: dict, *, goal: str) -> list:
    """The ``Sendable`` phrases for her ticked jobs, subject first; [] without the screen."""
    oq = _optional("puffpai.security.outbound_query")
    if oq is None:
        return []
    home = _GOALS.get(str(needs.get("goal_digest") or ""), (None, None))[1]
    allowed = [str(h.get("word")) for h in needs.get("held") or [] if h.get("allowed")]
    out: list = []
    seen: set[str] = set()
    subject = str((needs.get("subject") or {}).get("phrase") or "")
    try:
        subj = _subject_sendable(oq, goal, home, needs_rules.usable_subject(subject))
    except Exception:  # noqa: BLE001
        subj = None
    if subj is not None and oq.is_sendable(subj):
        out.append(subj)
        seen.add(subj.phrase)
    for job in needs.get("jobs") or []:
        if not job.get("ticked"):
            continue
        phrases = [s["phrase"] if isinstance(s, dict) else str(s)
                   for part in job.get("parts") or [] for s in part.get("search") or []]
        if not phrases:
            continue
        try:
            res = oq.screen(phrases, goal=goal, allowed_words=allowed, home=home,
                            source_hint=job["id"])
        except Exception:  # noqa: BLE001 - a screen that fails sends nothing
            continue
        for s in res.sendable:
            if oq.is_sendable(s) and s.phrase not in seen:
                seen.add(s.phrase)
                out.append(s)
    return out


__all__ = ["LINE_ASKED", "LINE_ASKING", "LINE_FAILED", "LINE_FROM_WORDS", "LINE_NO_BRAIN",
           "LINE_PRIVATE", "LINE_READ_DONE", "LINE_READING", "LINE_SLOW", "LINE_SMALL",
           "SCHEMA", "choose", "from_words", "get_helper", "goal_digest", "prewarm", "run",
           "sends"]
