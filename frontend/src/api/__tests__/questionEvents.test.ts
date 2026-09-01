import { afterEach, describe, expect, it, vi } from 'vitest';

import { subscribeToQuestionEvents } from '@/api/questionEvents';

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

describe('question event connection', () => {
  afterEach(() => {
    FakeEventSource.instances = [];
    vi.unstubAllGlobals();
  });

  it('shares one connection, validates the event, and releases the slot', () => {
    vi.stubGlobal('EventSource', FakeEventSource);
    const first = vi.fn();
    const second = vi.fn();
    const unsubscribeFirst = subscribeToQuestionEvents(first);
    const unsubscribeSecond = subscribeToQuestionEvents(second);

    expect(FakeEventSource.instances).toHaveLength(1);
    const source = FakeEventSource.instances[0];
    expect(source.url).toBe('/api/events/questions');
    source.onmessage?.(
      new MessageEvent('message', {
        data: JSON.stringify({
          type: 'question.changed',
          question_id: 'essay',
          status: 'ready',
          replacement_status: null,
        }),
      }),
    );
    expect(first).toHaveBeenCalledOnce();
    expect(second).toHaveBeenCalledOnce();

    unsubscribeFirst();
    expect(source.close).not.toHaveBeenCalled();
    unsubscribeSecond();
    expect(source.close).toHaveBeenCalledOnce();
  });
});

