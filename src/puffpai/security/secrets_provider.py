"""
PuffPAI Secrets Provider — single source of truth for service credentials.

Without this module, every service secret
(HA token, n8n key, Postgres/MinIO passwords, Anthropic, QuickBooks) is
read from plaintext ``.env`` files via ``os.getenv``. The Vault
(``vault.py``) protects user PII but is not the source of *any* service
secret, so the "vault is the only source" invariant is unimplemented.

This module is the migration path. It resolves a secret by name through an
ordered chain of providers and returns the first hit:

    1. PuffPAI Vault          — the appliance target (requires an unlocked
                                vault session; secrets live encrypted at rest)
    2. OS keychain            — Windows Credential Manager / macOS Keychain /
                                Secret Service, via the optional ``keyring`` lib
                                (good for the dev laptop; no plaintext on disk)
    3. Environment / .env     — legacy fallback, emits a deprecation warning so
                                the remaining plaintext usages are visible

It is intentionally NON-BREAKING: nothing imports it yet. Adopt it
incrementally by replacing ``os.getenv("HA_TOKEN")`` with
``get_secret("HA_TOKEN")``. Per-agent scoping is supported via
the ``scope`` argument so the Concierge can be denied device/cloud credentials.

No secret value is ever logged; only the resolving provider name is.
"""

from __future__ import annotations

import logging
import os
import warnings
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

# Optional OS keychain support — absence is fine, chain skips it.
try:
    import keyring  # type: ignore
    _HAS_KEYRING = True
except Exception:  # pragma: no cover - environment dependent
    _HAS_KEYRING = False

# The Vault import is deferred (see _vault_lookup) to avoid a hard dependency
# and any import cycle with security/__init__.

_KEYRING_SERVICE = "puffpai"


class AgentScope(Enum):
    """Which agent is asking. Enforces least privilege: no agent holds a
    credential it does not need.

    The first four scopes are the original three-agent roster (plus SYSTEM) and
    are retained verbatim for backward compatibility. The final five scopes,
    plus the seventh agent below, are the master-agent product team; Concierge
    is deliberately credential-free, mirroring the retired CORTEX scope.
    """

    CORTEX = "cortex"      # inference only — NO device or cloud credentials
    BRIDGE = "bridge"      # integration — HA + workflows + smart-home
    SENTINEL = "sentinel"  # security — audit ledger only, no outbound creds
    SYSTEM = "system"      # the app itself / setup — unrestricted
    # ── master-agent product team ───────────────────────────────────
    CONCIERGE = "concierge"    # WF-1 conversation — holds NO external secret
    STEWARD = "steward"        # WF-2 house ops — sole holder of HA + media creds
    ARCHIVIST = "archivist"    # WF-4 memory — sole holder of vault/chromadb creds
    MAJORDOMO = "majordomo"    # WF-5 proactive — sole holder of n8n + calendar
    CARETAKER = "caretaker"    # WF-6 self-maint — sole holder of service-control
    # ── seventh master agent ────────────────────────────────────────
    #: WF-7 generic tools. Deliberately credential-free, like CONCIERGE.
    #: The Toolsmith reads attacker-controlled web pages, so it must never be
    #: in the same privilege domain as the credential that opens the house: a
    #: page that talks it into a bad fetch cannot talk it into a door it has
    #: never held a key to.
    TOOLSMITH = "toolsmith"


# Per-agent allowlists. A secret not listed for a scope is refused even if it
# exists in a provider. SYSTEM is unrestricted. Extend as services are added.
#
# Some master-agent names below (e.g. VAULT_SERVICE_KEY, SERVICE_CONTROL_KEY) are
# forward-looking: today the vault is PIN-derived and service control is OS-level
# (systemd/Windows SCM), so those secrets are not yet populated in any provider.
# Listing them now fixes the *ownership boundary* — when the credential lands, it
# resolves for exactly one scope. Names the codebase resolves today
# (HA_TOKEN, JELLYFIN_API_KEY, N8N_API_KEY) match reality.
_SCOPE_ALLOW: dict[AgentScope, set[str]] = {
    AgentScope.CORTEX: set(),  # deliberately empty — inference needs no secrets
    AgentScope.BRIDGE: {
        "HA_TOKEN", "HA_ACCESS_TOKEN", "N8N_API_KEY", "OLLAMA_URL",
    },
    AgentScope.SENTINEL: {
        "PUFFPAI_AUDIT_KEY",
    },
    # ── master-agent product team (credentials) ─────────────────────
    AgentScope.CONCIERGE: set(),  # talks only to other agents — no secret
    AgentScope.STEWARD: {
        "HA_TOKEN", "HA_ACCESS_TOKEN", "JELLYFIN_API_KEY",
    },
    AgentScope.ARCHIVIST: {
        "VAULT_SERVICE_KEY", "CHROMADB_AUTH_TOKEN",
    },
    AgentScope.MAJORDOMO: {
        "N8N_API_KEY", "CALENDAR_API_KEY",
    },
    AgentScope.CARETAKER: {
        "SERVICE_CONTROL_KEY", "UPDATE_VERIFICATION_KEY",
    },
    # Empty on purpose and permanently. The Toolsmith is the one agent that
    # handles content an attacker wrote; giving it any secret would put a
    # prompt-injection surface and a credential in one process.
    AgentScope.TOOLSMITH: set(),
    # SYSTEM handled specially (no filtering).
}


class SecretNotFound(KeyError):
    """Raised when a secret cannot be resolved by any provider."""


class SecretAccessDenied(PermissionError):
    """Raised when an agent scope is not permitted a given secret."""


@dataclass
class SecretsProvider:
    """Ordered secret resolver. Construct once and reuse.

    Args:
        vault: an authenticated PuffPAI Vault instance, or None. If provided
            and unlocked, it is tried first.
        allow_env_fallback: if False, refuses to read from environment/.env
            (use in the shipped appliance to enforce vault-only).
    """

    vault: object | None = None
    allow_env_fallback: bool = True
    _warned: set[str] = field(default_factory=set, repr=False)

    # ── public API ──────────────────────────────────────────────────
    def get(
        self,
        name: str,
        *,
        scope: AgentScope = AgentScope.SYSTEM,
        default: Optional[str] = None,
        required: bool = False,
    ) -> Optional[str]:
        """Resolve a secret by name through the provider chain.

        Raises SecretAccessDenied if ``scope`` is not allowed the secret,
        SecretNotFound if ``required`` and nothing resolves.
        """
        self._check_scope(name, scope)

        for provider_name, lookup in (
            ("vault", self._vault_lookup),
            ("keyring", self._keyring_lookup),
            ("env", self._env_lookup),
        ):
            value = lookup(name)
            if value is not None:
                logger.debug("secret %r resolved via %s", name, provider_name)
                return value

        if required and default is None:
            raise SecretNotFound(
                f"secret {name!r} not found in vault, keyring, or environment"
            )
        return default

    # ── scope enforcement ───────────────────────────────────────────
    @staticmethod
    def _check_scope(name: str, scope: AgentScope) -> None:
        if scope is AgentScope.SYSTEM:
            return
        allowed = _SCOPE_ALLOW.get(scope, set())
        if name not in allowed:
            raise SecretAccessDenied(
                f"agent scope {scope.value!r} is not permitted secret {name!r}"
            )

    # ── providers ───────────────────────────────────────────────────
    def _vault_lookup(self, name: str) -> Optional[str]:
        # The Vault service-secret tier reads an encrypted ``service_secrets``
        # record via Vault.get_service_secret (defined in security/vault.py).
        # It returns None when the vault is locked or the secret is absent, so
        # the chain then falls through to keyring/env — this is a *lookup*, and
        # falling through is the intended (fail-open-for-read) behavior, not a
        # security downgrade: writes are never routed here.
        if self.vault is None:
            return None
        getter = getattr(self.vault, "get_service_secret", None)
        if not callable(getter):
            # A vault object was supplied but does not implement the tier.
            # Surface this once (not a silent no-op) and skip the tier.
            if "__vault_tier_unavailable__" not in self._warned:
                self._warned.add("__vault_tier_unavailable__")
                logger.warning(
                    "secrets vault-tier disabled: supplied vault lacks "
                    "get_service_secret(); resolving via keyring/env only"
                )
            return None
        try:
            return getter(name)
        except Exception as exc:  # never let vault errors leak or crash callers
            logger.debug("vault lookup for %r failed: %s", name, exc)
            return None

    def _keyring_lookup(self, name: str) -> Optional[str]:
        if not _HAS_KEYRING:
            return None
        try:
            return keyring.get_password(_KEYRING_SERVICE, name)
        except Exception as exc:  # pragma: no cover
            logger.debug("keyring lookup for %r failed: %s", name, exc)
            return None

    def _env_lookup(self, name: str) -> Optional[str]:
        if not self.allow_env_fallback:
            return None
        value = os.getenv(name)
        if value is not None and name not in self._warned:
            self._warned.add(name)
            warnings.warn(
                f"secret {name!r} was read from the environment/.env — this is "
                f"the legacy plaintext path. Migrate it "
                f"into the Vault or OS keychain.",
                DeprecationWarning,
                stacklevel=3,
            )
        return value


# ── module-level convenience singleton ──────────────────────────────
_default_provider: SecretsProvider | None = None


def get_default_provider() -> SecretsProvider:
    global _default_provider
    if _default_provider is None:
        _default_provider = SecretsProvider()
    return _default_provider


def set_default_provider(provider: SecretsProvider) -> None:
    """Install a provider (e.g. one holding an unlocked Vault) at startup."""
    global _default_provider
    _default_provider = provider


def install_unlocked_vault(vault: object) -> SecretsProvider:
    """Overlay an unlocked Vault onto the default provider chain.

    Reinstates the vault tier at the front of the chain (vault → keyring → env)
    by installing a provider that resolves service secrets through ``vault``
    first. Call this once a vault session is open so that ``get_secret`` prefers
    the encrypted store over the environment. Returns the installed provider.
    """
    provider = SecretsProvider(vault=vault)
    set_default_provider(provider)
    return provider


def get_secret(
    name: str,
    *,
    scope: AgentScope = AgentScope.SYSTEM,
    default: Optional[str] = None,
    required: bool = False,
) -> Optional[str]:
    """Shorthand for ``get_default_provider().get(...)``.

    Drop-in for ``os.getenv(name)`` — with vault/keychain precedence, per-agent
    scoping, and a deprecation warning whenever a value still comes from .env.
    """
    return get_default_provider().get(
        name, scope=scope, default=default, required=required
    )


__all__ = [
    "AgentScope",
    "SecretsProvider",
    "SecretNotFound",
    "SecretAccessDenied",
    "get_secret",
    "get_default_provider",
    "set_default_provider",
    "install_unlocked_vault",
]
