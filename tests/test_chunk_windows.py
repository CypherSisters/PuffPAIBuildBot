"""Articles keep their paragraphs and are cut into short passages."""
from __future__ import annotations

from puffpai.knowledge.chunking import chunk_windows
from puffpai.knowledge.ingestion import ContentSource, KnowledgeIngestion, TargetZone
from puffpai.knowledge.zim_reader import Article


def test_a_huge_article_with_no_newlines_becomes_many_short_passages():
    text = "Dinosaurs lived long ago and some were huge. " * 2400  # ~106k chars
    pieces = chunk_windows(text)
    assert all(len(p) <= 800 for p in pieces)
    assert 140 <= len(pieces) <= 170
    assert pieces[0].startswith("Dinosaurs") and pieces[-1].endswith("huge.")


def test_short_text_is_one_passage_and_blank_is_none():
    assert chunk_windows("A short note.") == ["A short note."]
    assert chunk_windows("   ") == []


def test_passages_overlap_so_no_sentence_is_lost():
    text = " ".join(f"Sentence number {n} is here." for n in range(200))
    pieces = chunk_windows(text, 300, 60)
    for a, b in zip(pieces, pieces[1:]):
        assert a[-30:].split()[-1] in b


def test_plain_text_keeps_paragraphs():
    html = ("<html><body><h1>Volcano</h1><p>A volcano   erupts.</p>"
            "<p>Magma rises.<br>Lava flows.</p><ul><li>Etna</li><li>Fuji</li></ul>"
            "<script>x()</script></body></html>")
    art = Article(url="Volcano", title="Volcano", mime_type="text/html",
                  content=html.encode(), namespace="C")
    text = art.plain_text
    assert "Volcano\n\nA volcano erupts.\n\nMagma rises.\n\nLava flows." in text
    assert "x()" not in text and "  " not in text


def test_knowledge_ingestion_never_makes_a_whole_article_one_passage():
    ing = KnowledgeIngestion()
    text = "Reptiles bask in the sun to keep warm. " * 500
    chunks = ing._chunk_text(text, "doc_1", "Reptile", ContentSource.ZIM_PACK,
                             TargetZone.KNOWLEDGE)
    assert len(chunks) > 20
    assert all(len(c.text) <= ing.CHUNK_SIZE + ing.CHUNK_OVERLAP + 2 for c in chunks)
