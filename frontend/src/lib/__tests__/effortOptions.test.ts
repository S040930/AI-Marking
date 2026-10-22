import { describe, expect, it } from 'vitest';

import {
  OPENAI_EFFORT_OPTIONS,
  SUBSCRIPTION_EFFORT_OPTIONS,
  effortOptionsFor,
} from '@/lib/effortOptions';

describe('effortOptionsFor', () => {
  it('订阅通道只提供实测档位', () => {
    expect(effortOptionsFor('chatgpt_sub')).toEqual(SUBSCRIPTION_EFFORT_OPTIONS);
    expect(effortOptionsFor('chatgpt_sub')).toContain('max');
    expect(effortOptionsFor('chatgpt_sub')).not.toContain('minimal');
  });

  it('官方通道与未知 provider 使用官方档位', () => {
    expect(effortOptionsFor('openai_api')).toEqual(OPENAI_EFFORT_OPTIONS);
    expect(effortOptionsFor(null)).toEqual(OPENAI_EFFORT_OPTIONS);
    expect(effortOptionsFor(undefined)).toEqual(OPENAI_EFFORT_OPTIONS);
  });
});
