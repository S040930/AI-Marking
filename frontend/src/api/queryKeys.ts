export const queryKeys = {
  questions: {
    all: ['questions'] as const,
    list: (search: string, limit: number) =>
      ['questions', { search, limit }] as const,
    gradingPrompt: (id: string | null) =>
      ['questions', id, 'grading-prompt'] as const,
  },
  submissions: {
    all: ['submissions'] as const,
    list: (page: number, pageSize: number) =>
      ['submissions', { page, pageSize }] as const,
    count: ['submissions-count'] as const,
    detail: (id: number | undefined, status: string) =>
      ['submission', id, status] as const,
    detailPrefix: (id: number) => ['submission', id] as const,
    status: (id: number | undefined) => ['submission-status', id] as const,
  },
  config: {
    all: ['config'] as const,
    profile: (id: number | undefined) => ['config', id ?? 'default'] as const,
    profiles: ['config-profiles'] as const,
  },
};

