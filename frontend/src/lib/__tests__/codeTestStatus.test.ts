import { describe, expect, it } from 'vitest';

import { degradedIsolation, isolationLabel, testStatusLabel } from '@/lib/codeTestStatus';

describe('codeTestStatus', () => {
  it('把已知状态映射为中文并原样返回未知值', () => {
    expect(testStatusLabel('passed')).toBe('通过');
    expect(testStatusLabel('not_run')).toBe('未自动执行');
    expect(testStatusLabel('unexpected' as never)).toBe('unexpected');
  });

  it('只在已知隔离等级时给出文案，缺失返回 null', () => {
    expect(isolationLabel('container:full')).toBe('容器隔离（完整）');
    expect(isolationLabel('seatbelt:partial')).toBe('沙箱隔离（受限）');
    expect(isolationLabel('future:partial')).toBe('future:partial');
    expect(isolationLabel(undefined)).toBeNull();
  });

  it('把 *:full 与缺失字段都视为未降级', () => {
    expect(degradedIsolation('container:full')).toBe(false);
    expect(degradedIsolation(undefined)).toBe(false);
    expect(degradedIsolation('seatbelt:partial')).toBe(true);
    expect(degradedIsolation('none')).toBe(true);
  });
});
