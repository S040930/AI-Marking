export const queryKeys = {
  questions: {
    all: ['questions'] as const,
    list: (search: string, limit: number) =>
      ['questions', { search, limit }] as const,
    gradingPrompt: (id: string | null) =>
      ['questions', id, 'grading-prompt'] as const,
    watch: (id: string | null) => ['questions', id, 'watch'] as const,
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
  },
  acp: {
    all: ['acp'] as const,
    agents: ['acp', 'agents'] as const,
    run: (id: number | undefined) => ['acp', 'run', id] as const,
    runEvents: (id: number | undefined) => ['acp', 'run-events', id] as const,
    defaultAgent: ['acp', 'default-agent'] as const,
    codexConfiguration: (modelId?: string | null) =>
      ['acp', 'codex-configuration', modelId ?? 'default'] as const,
    chat: {
      all: ['acp', 'chat'] as const,
      sessions: (submissionId: number | undefined) =>
        ['acp', 'chat', 'sessions', { submissionId }] as const,
      session: (id: number | undefined) =>
        ['acp', 'chat', 'session', id] as const,
      events: (id: number | undefined) =>
        ['acp', 'chat', 'session-events', id] as const,
    },
  },
};
