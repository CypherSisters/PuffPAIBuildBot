"""Every book on the summary says its size, switched on or left out.

"Left out: Motor Vehicle Mechanics" (324 MB) and
"Left out: iFixit repair guides" (3405 MB) showed no size, while other left-out
lines did. A left-out line shows only its label, so the size is in the label.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

from types import SimpleNamespace

from puffpai.wizard import proposal, render_cli

MB = 1024 * 1024


def _sctx():
    items = [{"id": "kiwix:mechanics", "label": "Motor Vehicle Mechanics",
              "bytes": 324 * MB, "selectable": True, "license": "CC-BY-SA-4.0",
              "what_it_is": "Car repair questions and answers.",
              "kiwix_book": True},
             {"id": "kiwix:unknown", "label": "Mystery pack", "bytes": None,
              "selectable": True}]
    return SimpleNamespace(previews={"sources": {"items": items, "selected": []}},
                           internet=True, args=SimpleNamespace(), goal="",
                           terms=[], mode="standard", name="CarBot")


def test_left_out_books_say_their_size():
    items = proposal._knowledge(_sctx())
    labels = {item["id"]: item["label"] for item in items}
    assert labels["knowledge:registry:kiwix:mechanics"] == \
        "Motor Vehicle Mechanics (about 324 MB)"
    assert labels["knowledge:registry:kiwix:unknown"] == "Mystery pack (size not known)"


def test_the_rendered_left_out_line_carries_the_size():
    item = next(i for i in proposal._knowledge(_sctx())
                if i["id"] == "knowledge:registry:kiwix:mechanics")
    item["on"] = False
    summary = {"sections": [{"id": "knowledge", "heading": "What it will know",
                             "items": [item]}], "reach": {}, "lines": {}}
    text = "\n".join(render_cli.summary_lines(summary))
    # The size moves out of the brackets to sit beside the reason.
    assert ("Not included (you can add it): Motor Vehicle Mechanics - about "
            "324 MB") in text
