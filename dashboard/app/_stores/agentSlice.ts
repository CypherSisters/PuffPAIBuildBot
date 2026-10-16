import { createSlice, createAsyncThunk, PayloadAction } from "@reduxjs/toolkit";
import {
  getAgentStatuses,
  getAgentActivity,
  sendAgentTask,
  type AgentStatus,
  type AgentActivity,
} from "../_lib/services/agentService";

interface AgentState {
  agents: AgentStatus[];
  activity: AgentActivity[];
  loading: boolean;
  error: string | null;
  taskSending: boolean;
}

const initialState: AgentState = {
  agents: [],
  activity: [],
  loading: false,
  error: null,
  taskSending: false,
};

export const fetchAgentStatuses = createAsyncThunk(
  "agents/fetchStatuses",
  async () => {
    const res = await getAgentStatuses();
    return res.agents;
  }
);

export const fetchAgentActivity = createAsyncThunk(
  "agents/fetchActivity",
  async (limit: number = 50) => {
    const res = await getAgentActivity(limit);
    return res.activity;
  }
);

export const submitAgentTask = createAsyncThunk(
  "agents/submitTask",
  async ({
    task,
    targetAgent,
    priority,
  }: {
    task: string;
    targetAgent?: string;
    priority?: string;
  }) => {
    const res = await sendAgentTask(task, targetAgent, priority);
    return res;
  }
);

const agentSlice = createSlice({
  name: "agents",
  initialState,
  reducers: {
    clearError: (state) => {
      state.error = null;
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(fetchAgentStatuses.pending, (state) => {
        state.loading = true;
      })
      .addCase(fetchAgentStatuses.fulfilled, (state, action) => {
        state.loading = false;
        state.agents = action.payload;
        state.error = null;
      })
      .addCase(fetchAgentStatuses.rejected, (state, action) => {
        state.loading = false;
        state.error = action.error.message || "Failed to fetch agent statuses";
      })
      .addCase(fetchAgentActivity.fulfilled, (state, action) => {
        state.activity = action.payload;
      })
      .addCase(submitAgentTask.pending, (state) => {
        state.taskSending = true;
      })
      .addCase(submitAgentTask.fulfilled, (state) => {
        state.taskSending = false;
      })
      .addCase(submitAgentTask.rejected, (state, action) => {
        state.taskSending = false;
        state.error = action.error.message || "Failed to submit task";
      });
  },
});

export const { clearError } = agentSlice.actions;
export default agentSlice.reducer;
