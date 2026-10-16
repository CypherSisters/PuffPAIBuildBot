import type { FlowReply } from "../_lib/services/wizardService";

/** Steps that come after the plan: the GitHub question is never asked there. */
const AFTER_PLAN = new Set(["hub", "vault", "build", "building", "done"]);

/**
 * Whether the wizard shows "Do you have a GitHub account?" now: once the goal
 * is answered, before the plan (where the searching happens), and only until
 * it is answered or skipped. An older backend that does not send `github`
 * never asks.
 */
export function githubDue(flow: FlowReply | null | undefined): boolean {
  if (!flow?.github || flow.github.asked || flow.approved) return false;
  if (!flow.next || flow.next === "first_look" || AFTER_PLAN.has(flow.next)) return false;
  const goal = (flow.questions ?? []).find((q) => q.id.endsWith(":goal"));
  return !!goal?.applied;
}

/** Steps where the Toolsmith question is never asked: the build is under way. */
const NOT_NOW = new Set(["first_look", "building", "done"]);

/**
 * Whether the wizard shows "Who should do Toolsmith's installs?" now: once the
 * goal and the GitHub question are answered, and only until it is answered or
 * skipped. Anthropic keeps its key in the vault, so it waits for the vault (set
 * when GitHub was connected, or at the vault step) and at the latest comes
 * just before the build starts. An older backend that does not send
 * `toolsmith` never asks.
 */
export function toolsmithDue(flow: FlowReply | null | undefined, githubAnswered: boolean): boolean {
  if (!flow?.toolsmith || flow.toolsmith.asked) return false;
  if (!flow.next || NOT_NOW.has(flow.next)) return false;
  if (flow.github && !flow.github.asked && !githubAnswered) return false;
  const goal = (flow.questions ?? []).find((q) => q.id.endsWith(":goal"));
  if (!goal?.applied) return false;
  return !!flow.vault?.ready || flow.next === "build";
}
