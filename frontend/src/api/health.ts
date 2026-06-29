import { useQuery } from '@tanstack/react-query';
import { apiClient } from './client';

export interface HealthResponse {
  status: string;
  timestamp: string;
}

export function useHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: () => apiClient.get<HealthResponse>('/health').then((r) => r.data),
    refetchInterval: 10_000,
  });
}
