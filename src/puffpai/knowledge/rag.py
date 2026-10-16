"""PuffPAI Dual-Zone RAG (Retrieval-Augmented Generation) — public reference.

Retrieves from two ChromaDB collections and labels every result with the zone it
came from, so the assistant (and the user) always knows whether a fact came from
*your vault* or *the knowledge base*:

  - Zone 1 (``puffpai_vault``): personal knowledge — auth-gated. Vault results are
    only returned when the caller is authenticated **and**, if a vault handle is
    wired in, that vault is currently unlocked. A locked vault is never queried.
  - Zone 2 (``puffpai_knowledge``): general knowledge from indexed packs — open.

This is a deliberately simple reference implementation: embed the query, take the
top-k from each permitted collection, and return the merged results ranked by
distance with a source label. It carries **no** query-gating policy engine and no
graph/state orchestration — the appliance build has those; this one does plain
retrieval and lets the Archivist agent own the zone/privacy policy.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

VAULT_COLLECTION = "puffpai_vault"
KNOWLEDGE_COLLECTION = "puffpai_knowledge"


class ResultSource(Enum):
    """Where a RAG result came from."""

    VAULT = "vault"          # Zone 1: personal knowledge
    KNOWLEDGE = "knowledge"  # Zone 2: general knowledge base


@dataclass
class RAGResult:
    """A single retrieval result with provenance."""

    text: str
    source: ResultSource
    title: str = ""
    score: float = 0.0
    metadata: dict = field(default_factory=dict)

    @property
    def provenance_label(self) -> str:
        """Human-readable source label for display."""
        if self.source == ResultSource.VAULT:
            return "from your vault"
        return "from knowledge base"

    @property
    def citation(self) -> str:
        """Which downloaded source this chunk came from, or "" if unlabelled.

        Only chunks indexed from a pack that arrived with a provenance sidecar
        carry these keys. An empty string means we genuinely do not know, and
        that is what gets shown: an answer that cannot name its source must not
        appear to have one. Every part of this string was written by us from
        the shipped source registry, never taken from the fetched text.
        """
        meta = self.metadata or {}
        label = str(meta.get("source_label") or meta.get("source_id") or "").strip()
        if not label:
            return ""
        licence = str(meta.get("source_license") or "").strip()
        return f"{label} ({licence})" if licence else label

    @property
    def source_id(self) -> str:
        """The registry id this chunk was indexed from, or "" if unlabelled."""
        return str((self.metadata or {}).get("source_id") or "").strip()


@dataclass
class RAGResponse:
    """Combined RAG query response with results from both zones."""

    query: str
    results: list  # list[RAGResult]
    vault_count: int = 0
    knowledge_count: int = 0

    @property
    def has_vault_results(self) -> bool:
        return self.vault_count > 0

    @property
    def has_knowledge_results(self) -> bool:
        return self.knowledge_count > 0

    def context_string(self, max_results: int = 8) -> str:
        """Build a context string for LLM prompting with provenance labels.

        Returns text like::

            [From your vault] Title: Medical Notes
            My doctor is Dr. Smith...

            [From knowledge base] Title: Python Programming
            Python is a high-level...
        """
        parts = []
        for r in self.results[:max_results]:
            header = f"[{r.provenance_label.title()}]"
            if r.title:
                header += f" Title: {r.title}"
            citation = r.citation
            if citation:
                header += f" Source: {citation}"
            parts.append(f"{header}\n{r.text}")
        return "\n\n---\n\n".join(parts)

    def cited_sources(self) -> list:
        """Distinct ``(source_id, citation)`` pairs behind these results.

        For showing an answer its receipts. Results with no provenance are not
        represented here at all, so a short list against a long result set means
        "some of this cannot be attributed" - never read it as "these are all
        the sources". :attr:`unattributed_count` is the other half of that
        sentence and should be shown with it.
        """
        seen: dict = {}
        for r in self.results:
            sid = r.source_id
            if sid and sid not in seen:
                seen[sid] = r.citation
        return sorted(seen.items())

    def attribution_lines(self) -> list:
        """A compact ASCII footer naming the sources behind these results.

        A footer, not a bibliography: one line of sources, plus one line about
        what could not be attributed when that is the case. The standing risk on
        fetched knowledge is a host serving wrong content - wrong dosages, wrong
        wiring - and the only detector is a person who can see which pack an
        answer came from. That is what these two lines are for.

        Chunks with no provenance are never given a placeholder label. They are
        counted and the count is stated, so a partly-attributed answer reads as
        partly attributed rather than as fully cited. Nothing retrieved means no
        footer at all - an answer the model wrote from its own weights is not
        given receipts it does not have.
        """
        if not self.results:
            return []
        cited = self.cited_sources()
        unattributed = self.unattributed_count
        total = len(self.results)
        passages = "passage" if total == 1 else "passages"
        lines = []
        if cited:
            lines.append("Sources: " + "; ".join(text for _sid, text in cited))
        if unattributed and cited:
            lines.append(f"{unattributed} of {total} retrieved {passages} carry no "
                         "source label, so that part of this answer is "
                         "unattributed.")
        elif unattributed:
            lines.append(f"The {total} retrieved {passages} carry no source label, "
                         "so I cannot tell you where this came from.")
        return lines

    @property
    def unattributed_count(self) -> int:
        """How many results carry no source label. Say it out loud; do not hide it."""
        return sum(1 for r in self.results if not r.source_id)


def chroma_settings():
    """The one Chroma client setting BuildBot uses: usage reports to Chroma's
    maker are off. Every client for the same folder must use the same
    settings, so every client is made with this."""
    from chromadb.config import Settings
    return Settings(anonymized_telemetry=False)


class DualZoneRAG:
    """Query the personal (vault) and general (knowledge) ChromaDB collections.

    Usage::

        rag = DualZoneRAG(vault=unlocked_vault)

        # Unauthenticated: only knowledge-base results
        response = rag.query("What is Python?")

        # Authenticated + unlocked vault: vault + knowledge results
        response = rag.query("What is my doctor's name?", authenticated=True)

        context = response.context_string()

    Collection handles may be injected directly (tests, or a pre-built store);
    otherwise a persistent ChromaDB client is created lazily from config and the
    two zone collections are opened (created on first use).
    """

    def __init__(self, vault_collection: Any = None,
                 knowledge_collection: Any = None,
                 db_path: str | None = None,
                 vault: Any = None,
                 client: Any = None) -> None:
        """Initialise the dual-zone RAG.

        Args:
            vault_collection: Zone-1 ChromaDB collection handle (optional; built
                lazily from config when omitted).
            knowledge_collection: Zone-2 ChromaDB collection handle (optional).
            db_path: ChromaDB persistent-storage directory. If ``None``, read
                ``paths.chromadb_path`` from config and resolve it against the
                data dir.
            vault: optional Phase-4 :class:`~puffpai.security.vault.Vault`. When
                present, its ``is_unlocked`` state gates every vault-zone query —
                a locked vault is never read even if ``authenticated=True``.
            client: optional pre-built ChromaDB client (mainly for tests).
        """
        self._vault_coll = vault_collection
        self._knowledge_coll = knowledge_collection
        self._db_path = db_path
        self._vault = vault
        self._client = client

    # ── ChromaDB client + collections (lazy) ─────────────────────────
    @property
    def client(self):
        """Lazy ``chromadb.PersistentClient`` rooted at the configured DB path."""
        if self._client is None:
            import chromadb

            from . import reading_helper

            # Chroma's own embedder may never fetch its model from the
            # internet: it is found on this computer or it is absent.
            reading_helper.install_guard()
            db_path = self._db_path or self._default_db_path()
            os.makedirs(db_path, exist_ok=True)
            self._client = chromadb.PersistentClient(path=db_path,
                                                     settings=chroma_settings())
        return self._client

    @staticmethod
    def _default_db_path() -> str:
        """Resolve the ChromaDB path from config (against the data dir)."""
        try:
            from puffpai.config import get_config
            cfg = get_config()
            p = cfg.paths.chromadb_path or "./kiwix_rag_db"
            return p if os.path.isabs(p) else os.path.join(cfg.DATA_DIR, p)
        except Exception:
            return os.path.abspath("kiwix_rag_db")

    @property
    def vault_collection(self):
        """Zone-1 collection handle (created on first use)."""
        if self._vault_coll is None:
            self._vault_coll = self.client.get_or_create_collection(
                name=VAULT_COLLECTION, metadata={"zone": VAULT_COLLECTION})
        return self._vault_coll

    @property
    def knowledge_collection(self):
        """Zone-2 collection handle (created on first use)."""
        if self._knowledge_coll is None:
            self._knowledge_coll = self.client.get_or_create_collection(
                name=KNOWLEDGE_COLLECTION,
                metadata={"zone": KNOWLEDGE_COLLECTION})
        return self._knowledge_coll

    # ── vault gating ─────────────────────────────────────────────────
    def _vault_readable(self) -> bool:
        """True if the vault zone may be queried this call.

        With no vault wired in, the caller's ``authenticated`` flag is the only
        gate. With a vault wired in, it must also be unlocked — a locked vault is
        never read, so personal data cannot leak out of a locked store.
        """
        if self._vault is None:
            return True
        return bool(getattr(self._vault, "is_unlocked", False))

    # ── query ────────────────────────────────────────────────────────
    def query(self, query_text: str, n_results: int = 5,
              authenticated: bool = False,
              vault_only: bool = False,
              knowledge_only: bool = False) -> RAGResponse:
        """Query the permitted zone(s) and return merged, ranked results.

        Args:
            query_text: the search query.
            n_results: max results per zone.
            authenticated: whether the caller has an active vault session. Vault
                results are excluded unless this is True.
            vault_only: query only the vault (still subject to auth + unlock).
            knowledge_only: query only the knowledge base.

        Returns:
            A :class:`RAGResponse` with provenance-labelled results.
        """
        all_results: list[RAGResult] = []

        # Zone 2 (knowledge) — always available unless the caller asked for vault only.
        if not vault_only:
            all_results.extend(self._query_collection(
                self.knowledge_collection, query_text, n_results,
                ResultSource.KNOWLEDGE))

        # Zone 1 (vault) — only when authenticated AND the vault is unlocked.
        if not knowledge_only and authenticated and self._vault_readable():
            all_results.extend(self._query_collection(
                self.vault_collection, query_text, n_results,
                ResultSource.VAULT))

        # Lower distance = closer match.
        all_results.sort(key=lambda r: r.score)

        vault_count = sum(1 for r in all_results if r.source == ResultSource.VAULT)
        knowledge_count = sum(
            1 for r in all_results if r.source == ResultSource.KNOWLEDGE)

        return RAGResponse(
            query=query_text,
            results=all_results,
            vault_count=vault_count,
            knowledge_count=knowledge_count,
        )

    @staticmethod
    def _query_collection(collection, query_text: str, n_results: int,
                          source: ResultSource) -> list:
        """Query one ChromaDB collection and wrap the hits with provenance.

        Any backend error (missing embedder, empty/corrupt collection) degrades to
        an empty result list rather than raising, so a partially-set-up knowledge
        store never breaks a chat turn.
        """
        try:
            results = collection.query(
                query_texts=[query_text], n_results=n_results)
        except Exception:
            return []

        if not results or not results.get("documents"):
            return []

        documents = results["documents"][0]
        distances = (results.get("distances") or [[]])[0]
        metadatas = (results.get("metadatas") or [[]])[0]

        wrapped = []
        for i, doc in enumerate(documents):
            meta = metadatas[i] if i < len(metadatas) else {}
            score = distances[i] if i < len(distances) else 999.0
            wrapped.append(RAGResult(
                text=doc,
                source=source,
                title=(meta or {}).get("title", ""),
                score=score,
                metadata=meta or {},
            ))
        return wrapped

    # ── inventory ────────────────────────────────────────────────────
    def stats(self) -> dict:
        """Return chunk counts per zone (0 on any error), for the CLI status view."""
        out = {"vault_chunks": 0, "knowledge_chunks": 0}
        try:
            out["knowledge_chunks"] = self.knowledge_collection.count()
        except Exception:
            pass
        try:
            out["vault_chunks"] = self.vault_collection.count()
        except Exception:
            pass
        return out


__all__ = [
    "ResultSource",
    "RAGResult",
    "RAGResponse",
    "DualZoneRAG",
    "VAULT_COLLECTION",
    "KNOWLEDGE_COLLECTION",
]
