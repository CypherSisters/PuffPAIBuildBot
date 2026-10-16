"""Drawbridge checks a live card before the owner is asked, and its own credit
line is not a web address slipped in by the service.

7Timer was the one card for "tells him when the skies are
clear". It sat on the summary as on, the owner said yes, and only then the
check refused it ("some of its words read like orders to a computer"). The
words were BuildBot's own credit line, "Data from 7Timer! stargazing forecast
(www.7timer.info).": the service's own host read as a web address.
"""
from __future__ import annotations

import json

from puffpai import artifact_review, drawbridge, feed_generator, feeds

SKY = {"dataseries": [{"cloudcover": 2, "seeing": 3, "transparency": 4}],
       "init": "2026092412", "product": "astro"}


def _seven_timer(**extra):
    base = {"id": "seed:seventimer", "kind": "live_data",
            "name": "7Timer! stargazing forecast",
            "what_it_is": "Stargazing forecast for your town: cloud cover, "
                          "steadiness of the air, and how clear the sky is.",
            "source_catalog": "api_seeds", "host": "www.7timer.info",
            "sample_endpoint": "https://www.7timer.info/bin/api.pl?lon=-83.0"
                               "&lat=40.0&product=astro&output=json",
            "auth": "none", "license": "free to use, not for commercial purposes",
            "commercial_ok": False, "match_terms": ["stargazing", "sky"],
            "card": {"kind": "value", "value": "dataseries[0].cloudcover",
                     "label": "Cloud cover (1 clear, 9 cloudy)",
                     "caption": "init"}}
    base.update(extra)
    return base


def _probe(document):
    body = json.dumps(document).encode()

    def probe(url, host, build_online, max_bytes):
        return {"status": 200, "content_type": "text/plain",
                "bytes": len(body), "truncated": False, "body": body}
    return probe


def test_buildbots_own_credit_line_is_not_a_slipped_in_address():
    manifest = {"name": "7Timer! stargazing forecast",
                "feed": {"service": {"name": "7Timer! stargazing forecast",
                                     "host": "www.7timer.info"},
                         "credit": "Data from 7Timer! stargazing forecast "
                                   "(www.7timer.info).",
                         "card": {"title": "Live from 7Timer!",
                                  "label": "Cloud cover"}}}
    rows = artifact_review.review({"manifest": manifest})
    words = next(r for r in rows if r["check"] == "outside text kept as data")
    assert words["result"] == "pass", words


def test_another_address_in_a_label_is_still_caught():
    manifest = {"name": "Sky", "feed": {
        "service": {"name": "Sky", "host": "www.7timer.info"},
        "credit": "Data from Sky (www.7timer.info).",
        "card": {"title": "Live", "label": "see www.evil.example for more"}}}
    rows = artifact_review.review({"manifest": manifest})
    words = next(r for r in rows if r["check"] == "outside text kept as data")
    assert words["result"] == "fail"


def test_the_look_before_the_plan_passes_the_sky_card(monkeypatch):
    monkeypatch.setattr(feeds, "home_block", lambda: {})
    got = feed_generator.preview(_seven_timer(), build_online=True,
                                 probe=_probe(SKY))
    assert got["ok"] is True and got["looked"] is True, got


def test_a_card_drawbridge_would_stop_is_left_off_before_the_yes(monkeypatch):
    monkeypatch.setattr(feeds, "home_block", lambda: {})
    real = drawbridge.check_addition
    seen = []

    def blocking(draft, **kw):
        seen.append(kw.get("record"))
        verdict = real(draft, **kw)
        return dict(verdict, verdict=drawbridge.BLOCK,
                    line="Sky: stopped because it failed a check.")

    monkeypatch.setattr(drawbridge, "check_addition", blocking)
    got = feed_generator.preview(_seven_timer(), build_online=True,
                                 probe=_probe(SKY))
    assert got == {"ok": False, "looked": True, "shows": "",
                   "why": "Sky: stopped because it failed a check."}
    assert seen == [False]  # a look before the plan is not a verdict on record
