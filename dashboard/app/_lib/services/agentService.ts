import { apiGet, apiPost } from "../api";

export interface AgentStatus {
  agent: string;
  status: string;
  task: string | null;
  timestamp: string | null;
  event?: string;
  task_id?: string;
  priority?: string;
}

export interface AgentActivity {
  agent: string;
  event: string;
  task?: string;
  task_id?: string;
  priority?: string;
  status?: string;
  timestamp: string;
}

export interface AgentHealth {
  agent: string;
  ok: boolean;
  scope?: string;
  [key: string]: boolean | string | number | null | undefined;
}

/** One capability an agent OWNS, as declared in the backend capability
 *  registry (the single source of truth the router reads). The card renders
 *  these as the agent's "Handles" list - what it actually does. */
export interface AgentCapability {
  id: string;
  domain: string;
  label: string;
  access: string; // "read" | "write" | "read-write"
  status: string; // "live" | "needs-auth" | "offline" | "planned"
  surfaces: string[];
  note?: string;
}

export interface TeamAgent {
  /** Internal id the bus routes on - never rename, never display raw. */
  name: string;
  /** What this build calls the agent (agents.name_preset / agents.names).
   *  Optional: an older backend does not send it, so callers fall back. */
  display_name?: string;
  scope: string;
  evolves_from: string;
  model_class: string;
  workflow: string;
  status: string;
  task: string | null;
  timestamp: string | null;
  health: AgentHealth;
  /** Declared capabilities this agent owns. Optional: an older backend does
   *  not send it, so the card renders the rest and omits the Handles list. */
  capabilities?: AgentCapability[];
  /** Its newest work, newest first (installs, pages, feeds, routines, the
   *  chat's tool calls). Optional: an older backend does not send it. */
  recent?: AgentRecent[];
}

export interface AgentRecent {
  task?: string;
  status?: string;
  timestamp?: string;
  tool?: string;
  /** Why it did not work (plain words, secrets hidden), when it did not. */
  reason?: string;
}

export interface AgentBus {
  agent_count: number;
  agents: string[];
  enabled: boolean;
  ok: boolean;
  reviewer: string;
  reviewer_display?: string;
  reviewer_wired: boolean;
}

export interface AgentStatusResponse {
  agents: AgentStatus[];
  team?: TeamAgent[];
  bus?: AgentBus;
}

export interface AgentTaskResult {
  success: boolean;
  task: {
    id: string;
    task: string;
    routed_to: string;
    priority: string;
    status: string;
    result?: {
      action?: string;
      success?: boolean;
      result?: Record<string, any>;
      error?: string;
      info?: string;
    };
  };
}

export async function getAgentStatuses(): Promise<AgentStatusResponse> {
  return apiGet<AgentStatusResponse>("/agents/status");
}

export async function getAgentActivity(
  limit = 50
): Promise<{ activity: AgentActivity[] }> {
  return apiGet<{ activity: AgentActivity[] }>(
    `/agents/activity?limit=${limit}`
  );
}

export async function sendAgentTask(
  task: string,
  targetAgent = "auto",
  priority = "medium",
  params?: Record<string, any>
): Promise<AgentTaskResult> {
  return apiPost<AgentTaskResult>("/agents/task", {
    task,
    target_agent: targetAgent,
    priority,
    ...(params ? { params } : {}),
  });
}
