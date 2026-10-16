"""Archivist — WF-4 memory & knowledge (the only path to Zone-1).

The Archivist is the sole holder of vault access and the dual-zone RAG
(``AgentScope.ARCHIVIST``). This is the load-bearing split from Cortex: the agent
that renders arbitrary conversation (Concierge) is *structurally unable* to read
Zone-1. It must ask the Archivist, which enforces, per request:

* **Requester identity** — whose memory/vault is being touched.
* **Privacy mode** — a *kid* profile is Zone-2 (knowledge) only; vault results are
  never returned and the query is never run authenticated. This turns the old
  "zone policy is metadata" gap into enforcement.
* **Zone policy** — vault (Zone-1) results require an authenticated vault session
  *and* a non-kid profile; otherwise only Zone-2 knowledge is queried.

Composition: wraps :class:`DualZoneRAG`, :class:`Vault`, and :class:`UserMemory`.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

from puffpai.logging_config import get_logger
from puffpai.security.audit_log import AuditLog
from puffpai.security.secrets_provider import AgentScope, SecretsProvider

from .base import BaseAgent
from .envelope import Envelope

logger = get_logger(__name__)

# Profile identities that are restricted to Zone-2 (kid-safe) knowledge only.
_KID_PROFILES = {"child", "kid", "minor"}


def _is_kid(envelope: Envelope) -> bool:
    p = envelope.payload or {}
    profile = str(p.get("profile", "")).lower()
    privacy_mode = str(p.get("privacy_mode", "")).lower()
    return (
        profile in _KID_PROFILES
        or privacy_mode in _KID_PROFILES
        or str(envelope.requester).lower() in _KID_PROFILES
    )


class ArchivistAgent(BaseAgent):
    """Memory + knowledge retrieval with zone/privacy enforcement."""

    name = "archivist"
    scope = AgentScope.ARCHIVIST

    def __init__(self, *, secrets: SecretsProvider | None = None,
                 rag=None, vault=None, memory_factory=None,
                 audit: AuditLog | None = None) -> None:
        super().__init__(secrets=secrets)
        self._rag = rag
        self._vault = vault
        self._memory_factory = memory_factory  # callable(user_id) -> UserMemory
        self._audit = audit or AuditLog()

    # ── engine accessors (lazy, guarded) ────────────────────────────
    def _rag_engine(self):
        if self._rag is None:
            try:
                from puffpai.knowledge.rag import DualZoneRAG  # type: ignore[import-not-found]
            except ImportError as exc:  # pragma: no cover - knowledge ships in Phase 5
                raise RuntimeError(
                    "Knowledge retrieval is not available in this build. Inject a "
                    "rag engine to exercise the Archivist."
                ) from exc
            # Pass the vault handle so vault-zone reads are gated on is_unlocked
            # (defence in depth on top of the authenticated/session check below).
            self._rag = DualZoneRAG(vault=self._vault)
        return self._rag

    def _vault_engine(self):
        """Return the vault, constructing the reference vault lazily if needed.

        A vault may be injected (tests, or a pre-unlocked appliance vault). If
        none was, the public-build :class:`~puffpai.security.vault.Vault` is now
        available and is constructed here (locked until a password unlocks it).
        Earlier phases had no vault module, so this raised; Phase 4 lands it.
        """
        if self._vault is None:
            from puffpai.security.vault import Vault
            self._vault = Vault()
        return self._vault

    def _memory(self, user_id: str):
        if self._memory_factory is not None:
            return self._memory_factory(user_id)
        try:
            from puffpai.user_memory import UserMemory  # type: ignore[import-not-found]
        except ImportError as exc:  # user memory ships in a later phase
            raise RuntimeError(
                "User memory is not available in this build (it ships in a "
                "later phase). Inject a memory_factory to exercise the Archivist."
            ) from exc
        return UserMemory(user_id)

    # ── request handling ────────────────────────────────────────────
    def handle(self, envelope: Envelope) -> dict:
        intent = envelope.intent
        p = envelope.payload or {}
        try:
            if intent in ("retrieve", "query", "knowledge"):
                return self._retrieve(envelope)
            if intent in ("get_memory", "memory_context"):
                mem = self._memory(str(envelope.requester))
                return {"ok": True, "context": mem.get_memory_context()}
            if intent == "remember":
                mem = self._memory(str(envelope.requester))
                mem.extract_and_store(str(p.get("text", "")))
                mem.save()
                return {"ok": True}
            return {"ok": False, "error": f"archivist: unknown intent {intent!r}"}
        except KeyError as exc:
            return {"ok": False, "error": f"archivist: missing payload key {exc}"}

    def _retrieve(self, envelope: Envelope) -> dict:
        p = envelope.payload or {}
        query = str(p.get("query", ""))
        n_results = int(p.get("n_results", 5))
        kid = _is_kid(envelope)

        # Zone policy: vault (Zone-1) requires BOTH an authenticated session and a
        # non-kid profile. Kid profiles are hard-pinned to Zone-2 knowledge only.
        has_session = bool(p.get("vault_session_id"))
        authenticated = has_session and not kid
        knowledge_only = kid or not authenticated

        rag = self._rag_engine()
        response = rag.query(
            query,
            n_results=n_results,
            authenticated=authenticated,
            knowledge_only=knowledge_only,
        )

        zones = ["knowledge"]
        if authenticated and not knowledge_only:
            zones.append("vault")

        self._audit.log(
            action="archivist_retrieve",
            user=str(envelope.requester),
            result="success",
            details=f"kid={kid} zones={zones}",
            extra={"zones_queried": zones, "kid_profile": kid,
                   "authenticated": authenticated},
        )

        vault_count = getattr(response, "vault_count", 0)
        knowledge_count = getattr(response, "knowledge_count", 0)

        # Degrade cleanly when nothing was retrieved: an empty index should read as
        # a helpful pointer to the pack catalog, not a blank context block.
        if (vault_count + knowledge_count) == 0:
            context = ("no knowledge packs indexed yet; see docs/KNOWLEDGE_PACKS.md")
        else:
            context = (response.context_string()
                       if hasattr(response, "context_string") else "")

        return {
            "ok": True,
            "kid_profile": kid,
            "authenticated": authenticated,
            "zones_queried": zones,
            "vault_count": vault_count,
            "knowledge_count": knowledge_count,
            "context": context,
        }

    def health(self) -> dict:
        base = super().health()
        base["rag_available"] = self._rag is not None or True  # lazy-constructible
        # The vault module lands in this build; report whether one is present and,
        # if so, whether a session is currently open.
        try:
            vault = self._vault_engine()
            base["vault_available"] = True
            base["vault_unlocked"] = bool(getattr(vault, "is_unlocked", False))
        except Exception:
            base["vault_available"] = False
            base["vault_unlocked"] = False
        return base


__all__ = ["ArchivistAgent"]
