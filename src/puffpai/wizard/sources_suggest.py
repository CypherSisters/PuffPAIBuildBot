"""Search words from the local model, filtered like the owner's own words.

The model is not asked to name websites: those were shown on the card as "not
vetted: add it yourself", a dead end, and a model choosing addresses. Now the model only suggests *search words*. Code searches the
catalogs with them (:mod:`puffpai.catalogs.knowledge_resolver`) and picks among
what the catalogs actually list; every result cites the catalog words that
matched. The model cannot add an id, a URL or a file to anything.

The client is injected or built from what Discover saw; with neither, there
are no extra words and the search runs on the owner's goal alone.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from puffpai.wizard import sources_llm

#: What the card says when the model was asked and nothing usable came back.
#: A quiet line, never an error: the extra words were optional.
QUIET_NOTE = ("The local model did not suggest extra search words this time, so "
              "only your own words were used.")


def default_client(url: str = "", model: str = ""
                   ) -> Optional[Callable[[str], Any]]:
    """The model client Sources asks: *model* on the Ollama at *url*.

    ``None`` without both, which is what makes extra search words absent rather
    than invented on a build whose brain is not there yet.
    """
    if not str(url or "").strip() or not str(model or "").strip():
        return None
    return sources_llm.client(url, model)


def search_terms(goal: str, *, client: Optional[Callable[[str], Any]] = None
                 ) -> dict[str, Any]:
    """``{"terms": [...], "note": str}`` - the model's search words for *goal*.

    Every word goes through :func:`sources_llm.clean_term` and then the same
    filter as the owner's words (:func:`goal_words.goal_terms`), so filler and
    anything address-shaped never reach a search. The note is set only when a
    client was asked and could not answer.
    """
    from puffpai.catalogs import goal_words

    goal = str(goal or "").strip()
    if not goal or client is None:
        return {"terms": [], "note": ""}
    try:
        answer = client(goal)
        raw = sources_llm.parse_terms(answer) if not isinstance(answer, list) \
            else [t for t in (sources_llm.clean_term(a) for a in answer) if t]
    except Exception:  # noqa: BLE001 - a model that will not answer adds nothing
        return {"terms": [], "note": QUIET_NOTE}
    own = set(goal_words.goal_terms(goal))
    terms = [t for t in goal_words.goal_terms("", extra=raw) if t not in own]
    return {"terms": terms[:sources_llm.MAX_TERMS],
            "note": "" if terms else QUIET_NOTE}


def suggest(goal: str, *, online: bool = False,
            client: Optional[Callable[[str], Any]] = None) -> list[dict[str, Any]]:
    """Kept for older callers, with today's true answer: no websites, ever.

    The model used to name websites here. Now it only suggests
    search words (:func:`search_terms`), so this always returns an empty list
    and never calls a model.
    """
    return []


__all__ = ["QUIET_NOTE", "default_client", "search_terms", "suggest"]
