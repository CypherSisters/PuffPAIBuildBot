import { apiGet } from '../api';
import type { SystemStatus } from '../../_interfaces/api';

export async function getSystemStatus(): Promise<SystemStatus> {
  const res = await apiGet<{ success: boolean } & SystemStatus>('/system/status');
  return res;
}

