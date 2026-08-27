import type { ReactNode } from 'react';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { useSubmissionEvents } from '@/api/submissions';

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  close = vi.fn();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }
}

function wrapper({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={new QueryClient()}>
      {children}
    </QueryClientProvider>
  );
}

describe('useSubmissionEvents', () => {
  afterEach(() => {
    FakeEventSource.instances = [];
    vi.unstubAllGlobals();
  });

  it('进入终态后关闭 EventSource', () => {
    vi.stubGlobal('EventSource', FakeEventSource);
    const { rerender } = renderHook(
      ({ enabled }) => useSubmissionEvents(7, enabled),
      { initialProps: { enabled: true }, wrapper },
    );
    const source = FakeEventSource.instances[0];
    expect(source.url).toBe('/api/submissions/7/events');

    rerender({ enabled: false });

    expect(source.close).toHaveBeenCalledOnce();
    expect(FakeEventSource.instances).toHaveLength(1);
  });
});

describe('useSubmissionEvents close-on-terminal-event', () => {
  afterEach(() => {
    FakeEventSource.instances = [];
    vi.unstubAllGlobals();
  });

  it('收到终态事件后立即关闭 EventSource', () => {
    vi.stubGlobal('EventSource', FakeEventSource);
    renderHook(() => useSubmissionEvents(7, true), { wrapper });
    const source = FakeEventSource.instances[0];
    expect(source.url).toBe('/api/submissions/7/events');

    // 模拟 SSE 推送终态事件
    const event = new MessageEvent('message', {
      data: JSON.stringify({ submission_id: 7, status: 'reviewed' }),
    });
    source.onmessage?.(event);

    expect(source.close).toHaveBeenCalledOnce();
  });

  it('收到非终态事件不关闭 EventSource', () => {
    vi.stubGlobal('EventSource', FakeEventSource);
    renderHook(() => useSubmissionEvents(7, true), { wrapper });
    const source = FakeEventSource.instances[0];

    const event = new MessageEvent('message', {
      data: JSON.stringify({ submission_id: 7, status: 'ocr_processing' }),
    });
    source.onmessage?.(event);

    expect(source.close).not.toHaveBeenCalled();
  });
});
