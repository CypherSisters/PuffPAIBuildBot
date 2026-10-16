import { apiGet, apiPut, apiPost } from '../api';

export type AppName = 'alarmflow' | 'calendar' | 'freshtable' | 'fitfam' | 'trivia';

export async function getAppData(appName: AppName): Promise<any> {
  return apiGet(`/apps/${appName}/data`);
}

export async function setAppData(appName: AppName, data: any): Promise<{ success: boolean }> {
  return apiPut(`/apps/${appName}/data`, data);
}

export async function appAction(appName: AppName, action: string, params?: any): Promise<any> {
  return apiPost(`/apps/${appName}/action`, { action, params: params || {} });
}

export async function getAppUpdateStatus(appName: AppName): Promise<{ last_updated: string }> {
  return apiGet(`/apps/${appName}/updated`);
}
