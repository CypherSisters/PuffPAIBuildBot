// What POST /setup/vault's 409 means, in words the owner can act on.
//
// WHY THIS EXISTS: the wizard treated `reply.status === 409` as success. The
// backend's 409 (`already_initialized`, api_server.py `setup_create_vault`)
// means the opposite of success: a vault was already sealed on this box, the
// password that was just typed was NOT used for anything, and no recovery code
// was issued. The wizard marked the vault "created" and moved on, so the owner
// walked away believing a PIN they invented five seconds ago opens the vault.
// It does not, and they find that out on the day they need it.
//
// Pure functions and constants only — no React, no fetch — so the mapping is
// unit tested (app/_lib/__tests__/setup-gate.test.mjs).

/** Shaped like setupService's Reply for the vault call, minus the dependency. */
export interface VaultReply {
  ok: boolean;
  status: number;
  data?: { already_initialized?: boolean; message?: string } | null;
}

/**
 * True when the backend refused because a vault already exists. Keyed on the
 * status code AND the flag: a 409 is the contract, the flag is the backend
 * saying which 409 this is, and either alone is enough to be honest about it.
 */
export function isVaultAlreadyInitialized(reply: VaultReply): boolean {
  if (reply.ok) return false;
  return reply.status === 409 || reply.data?.already_initialized === true;
}

/** The headline: what happened to the password they just typed. */
export const VAULT_EXISTS_TITLE =
  "A vault already exists on this box; the PIN you typed was not used.";

/** The two ways forward, neither of them silent. */
export const VAULT_EXISTS_BODY =
  "Nothing was created and no recovery code was issued. Unlock the existing " +
  "vault with the PIN it was sealed with. Or, if that PIN and its recovery " +
  "code are both gone, reset it from the command line. A reset destroys " +
  "everything stored in the vault, including any saved Home Assistant token. " +
  "docs/TROUBLESHOOTING.md, “Vault PIN forgotten”, is the long version.";

/**
 * The reset, as real commands. There is deliberately no `puffpai vault reset`
 * verb (`cli.py` offers status / unlock / lock / store / get), because a verb
 * that erases secrets in one line is the wrong shape for this; the documented
 * path deletes the vault directory by hand. Shell-neutral, one step per line.
 */
export const VAULT_RESET_COMMANDS = [
  "puffpai vault status",
  "# then, to start over, delete the vault_data folder under your data dir",
  "#   (PUFFPAI_DATA_DIR if you set it, otherwise the repo root)",
  "puffpai setup",
];

/** The acknowledgement the owner ticks before Continue will move on. */
export const VAULT_EXISTS_ACK =
  "I understand: the PIN I just typed is not the vault's PIN.";
