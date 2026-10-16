"""Is this find a page ABOUT the topic, not a tool a person uses?

The first relevance question. Invitation sites, website
templates, a couple's own wedding site, homework and class projects all sound
exactly like the topic, and ranking by "does the blurb sound like the topic"
put them above the one working planner. This check reads only the author's own
summary (repository name, description, topics) and is goal-agnostic:

* strong hits ("our wedding", "landing page", "website template", "template
  for", "showcase", "homework", "class project", "<x> clone", "tutorial", page
  topics such as ``landing-page``) always mean page;
* weak hits (wedding, digital or online invitations) are cancelled when the
  description also names a tool (planner, tracker, manager, dashboard, ...).

Measured on the recorded finds: 16 of 44 wedding star-floor finds flagged (all
invitation or website templates, or a couple's own site), 0 of 279 in nine
other goals; EazyWed (a planning platform that mentions invitations) is not.

Link lists and articles count too ("awesome list", "curated list of apps", "blog post about"). A
static app that stores nothing is NOT a page: a chess board, a timer or a
metronome is still a tool.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import re

_STRONG = re.compile(
    r"\b(our|my|his|her|their)\s+(own\s+)?(wedding|big day|website|site|invitation|party|birthday|portfolio|blog|homepage)\b"
    r"|\bsave[- ]the[- ]date\b|\blanding[- ]page\b|\bportfolio (website|site|page)\b|\bpersonal (site|website|page|homepage)\b"
    r"|\b(website|site|html|bootstrap|bulma|tailwind|css|jekyll|hugo|wordpress)\s+(template|theme|starter)\b"
    r"|\btemplate\s+(for|website|site)\b|\b(website|site|page)\s+(for|of)\s+(a|an|the|my|our)\b"
    r"|\bshowcase\b|\bhomework\b|\bassignment\b"
    r"|\b(course|class|school|college|university|capstone|bootcamp|group|major|final|mini)\s+project\b"
    r"|\bproject\s*#?\s*\d\b|\bclone\s+of\b|\b\w+\s+clone\b|\btutorial\b"
    # A link list or an article is a page too: "awesome list",
    # "curated list of apps", "list of resources", "blog post about".
    r"|\bawesome[- ]list\b|\bcurated\s+(?:list|collection)\b"
    r"|\blist\s+of\s+(?:\w+\s+)?(?:links|resources|tools|apps|applications|projects|libraries|software)\b"
    r"|\bblog\s+post\b|\barticles?\s+(?:about|on)\b", re.I)
_WEAK = re.compile(r"\b(wedding|digital|online|e-?)\s*invitations?\b"
                   r"|\binvitation (card|website|site|template|app|page)\b", re.I)
_TOOL = re.compile(r"\b(planner|planning (app|platform|tool|software)|tracker|tracking|manager|management|organi[sz]er|"
                   r"calculator|dashboard|scheduler|inventory|budget(ing)?|checklist|to-?do|log(book)?|editor|generator)\b", re.I)
_STRONG_TOPICS = frozenset({"website-template", "landing-page", "portfolio-website", "wedding-website",
                            "wedding-page", "personal-website", "html-template", "bootstrap-template",
                            "website-theme", "homework", "assignment", "school-project", "college-project",
                            "university-project", "clone", "tutorial", "awesome-list", "awesome"})
_WEAK_TOPICS = frozenset({"invitation", "wedding-invitation", "digital-invitation", "digital-invitations"})


def looks_like_page(name: str, description: str, topics=()) -> list[str]:
    """The page words found (empty when it reads like a tool)."""
    text = f"{str(name or '').replace('-', ' ').replace('_', ' ')}. {description or ''}"
    tops = {str(t).lower() for t in topics or ()}
    strong = [m.group(0).lower() for m in _STRONG.finditer(text)] + sorted(tops & _STRONG_TOPICS)
    if strong:
        return strong
    weak = [m.group(0).lower() for m in _WEAK.finditer(text)] + sorted(tops & _WEAK_TOPICS)
    if weak and not _TOOL.search(description or ""):
        return weak
    return []


__all__ = ["looks_like_page"]
