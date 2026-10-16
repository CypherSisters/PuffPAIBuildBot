"""The bot's own topic word must not turn a question into an empty-list reply.

A scaffolded persona keeps lists named after the goal's topic word
("dinosaurs", "albums", "space"). Without care, "what is the biggest dinosaur
that ever lived?" is answered "The dinosaurs list is empty." before the model
or the live cards are asked. The list shortcut answers only when the
sentence clearly means the list.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import pytest

from puffpai import app_fastpath


def _bot(app_id, lists):
    return {"schema": "puffpai_app/v1", "id": app_id, "name": app_id,
            "data": {"default": {key: [] for key in lists}},
            "vocabulary": {"keywords": [app_id] + list(lists)}}


class Store:
    def __init__(self, documents=None):
        self.documents = documents or {}

    def get_data(self, app_id):
        return dict(self.documents.get(app_id, {}))

    def set_data(self, app_id, document):
        self.documents[app_id] = dict(document)
        return True


DINO = _bot("dinosaurbot", ["dinosaurs"])
MUSIC = _bot("musicbot", ["albums", "bands"])
SPACE = _bot("stargazerbot", ["space"])
CRAFT = _bot("knittingbot", ["crochet"])

TOPIC_QUESTIONS = [
    (DINO, "what is the biggest dinosaur that ever lived?"),
    (DINO, "show me museum dinosaur specimens"),
    (DINO, "tell me about dinosaurs"),
    (DINO, "give me a dinosaur fact"),
    (MUSIC, "what albums did Nirvana release?"),
    (MUSIC, "show me a band on Deezer"),
    (SPACE, "show me cool space stuff"),
    (CRAFT, "show me a crochet pattern"),
    (DINO, "I want to drop by the dinosaur museum"),
]


@pytest.mark.parametrize("manifest,message", TOPIC_QUESTIONS)
def test_topic_questions_fall_through(manifest, message):
    assert app_fastpath.handle(message, Store(), manifests=[manifest]) is None


@pytest.mark.parametrize("manifest,message", TOPIC_QUESTIONS)
def test_topic_questions_fall_through_even_with_rows(manifest, message):
    key = next(iter(manifest["data"]["default"]))
    store = Store({manifest["id"]: {key: [{"item": "stegosaurus"}]}})
    assert app_fastpath.handle(message, store, manifests=[manifest]) is None


def test_the_list_itself_still_answers():
    store = Store({"dinosaurbot": {"dinosaurs": []}})
    assert app_fastpath.handle("what is on the dinosaurs list?", store,
                               manifests=[DINO]) == "The dinosaurs list is empty."
    assert app_fastpath.handle("add t-rex to my dinosaurs", store,
                               manifests=[DINO]).startswith("Added t-rex")
    assert app_fastpath.handle("show me my dinosaurs", store,
                               manifests=[DINO]) == "On the dinosaurs list: t-rex."
    assert app_fastpath.handle("remove t-rex from the dinosaurs list", store,
                               manifests=[DINO]).startswith("Took t-rex off")


def test_my_list_without_the_word_list_and_empty_falls_through():
    store = Store({"dinosaurbot": {"dinosaurs": []}})
    assert app_fastpath.handle("show me my dinosaurs", store, manifests=[DINO]) is None
