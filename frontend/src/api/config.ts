import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient, responseData } from './client';
import { queryKeys } from './queryKeys';
import type { components } from './generated';

export type ConfigOut = components['schemas']['ConfigOut'] & {
  rubric_definition: components['schemas']['RubricDefinition'] | null;
};
export type ConfigUpdate = components['schemas']['ConfigUpdate'];

// hooks
export function useConfig() {
  return useQuery<ConfigOut>({
    queryKey: queryKeys.config.all,
    queryFn: () => responseData(apiClient.get<ConfigOut>('/config')),
  });
}

export function useUpdateConfig() {
  const queryClient = useQueryClient();
  return useMutation<ConfigOut, Error, ConfigUpdate>({
    mutationFn: (payload: ConfigUpdate) =>
      responseData(apiClient.put<ConfigOut>('/config', payload)),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.config.all, data);
      queryClient.invalidateQueries({ queryKey: queryKeys.config.all });
    },
  });
}
