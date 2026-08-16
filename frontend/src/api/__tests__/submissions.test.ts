import { describe, it, expect } from 'vitest';
import {
  canLoadSubmissionDetail,
  isTerminal,
  isProcessing,
  type SubmissionStatus,
} from '@/api/submissions';

describe('isTerminal', () => {
  it('ready_for_review 为终态', () => {
    expect(isTerminal('ready_for_review')).toBe(true);
  });

  it('reviewed 为终态', () => {
    expect(isTerminal('reviewed')).toBe(true);
  });

  it('failed 为终态', () => {
    expect(isTerminal('failed')).toBe(true);
  });

  it('awaiting_mcp 仍需等待外部助手，不是终态', () => {
    expect(isTerminal('awaiting_mcp')).toBe(false);
  });

  it('awaiting_mcp 可加载恢复页详情', () => {
    expect(canLoadSubmissionDetail('awaiting_mcp')).toBe(true);
  });

  const nonTerminal: SubmissionStatus[] = [
    'pending',
    'ocr_processing',
    'ocr_done',
    'awaiting_mcp',
  ];

  nonTerminal.forEach((status) => {
    it(`${status} 不是终态`, () => {
      expect(isTerminal(status)).toBe(false);
    });
  });
});

describe('isProcessing', () => {
  it('ready_for_review 不是处理中', () => {
    expect(isProcessing('ready_for_review')).toBe(false);
  });

  it('reviewed 不是处理中', () => {
    expect(isProcessing('reviewed')).toBe(false);
  });

  it('failed 不是处理中', () => {
    expect(isProcessing('failed')).toBe(false);
  });

  const processing: SubmissionStatus[] = [
    'pending',
    'ocr_processing',
    'ocr_done',
  ];

  processing.forEach((status) => {
    it(`${status} 是处理中`, () => {
      expect(isProcessing(status)).toBe(true);
    });
  });
});

describe('isTerminal 与 isProcessing 互斥', () => {
  const allStatuses: SubmissionStatus[] = [
    'pending',
    'ocr_processing',
    'ocr_done',
    'awaiting_mcp',
    'ready_for_review',
    'reviewed',
    'failed',
  ];

  allStatuses.forEach((status) => {
    it(`${status}: isTerminal !== isProcessing`, () => {
      expect(isTerminal(status)).not.toBe(isProcessing(status));
    });
  });
});
