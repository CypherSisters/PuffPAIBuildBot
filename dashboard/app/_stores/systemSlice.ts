import { createSlice, createAsyncThunk } from '@reduxjs/toolkit';
import { getSystemStatus } from '../_lib/services/systemService';
import type { SystemStatus } from '../_interfaces/api';

interface SystemState {
  status: SystemStatus | null;
  backendConnected: boolean;
  loading: boolean;
  error: string | null;
}

const initialState: SystemState = {
  status: null,
  backendConnected: false,
  loading: false,
  error: null,
};

export const fetchSystemStatus = createAsyncThunk('system/fetchStatus', async () => {
  return getSystemStatus();
});

const systemSlice = createSlice({
  name: 'system',
  initialState,
  reducers: {
    setBackendConnected: (state, action) => {
      state.backendConnected = action.payload;
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(fetchSystemStatus.pending, (state) => { state.loading = true; state.error = null; })
      .addCase(fetchSystemStatus.fulfilled, (state, action) => {
        state.loading = false;
        state.status = action.payload;
        state.backendConnected = true;
      })
      .addCase(fetchSystemStatus.rejected, (state, action) => {
        state.loading = false;
        state.error = action.error.message || 'Failed to fetch status';
        state.backendConnected = false;
      });
  },
});

export const { setBackendConnected } = systemSlice.actions;
export default systemSlice.reducer;
