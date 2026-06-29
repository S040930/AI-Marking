import { describe, it, expect } from 'vitest';
import { cn } from '@/lib/utils';

describe('cn', () => {
  it('合并多个类名字符串', () => {
    expect(cn('foo', 'bar')).toBe('foo bar');
  });

  it('过滤假值', () => {
    expect(cn('foo', false, null, undefined, '', 'bar')).toBe('foo bar');
  });

  it('处理条件类名对象', () => {
    expect(cn('base', { active: true, hidden: false })).toBe('base active');
  });

  it('处理类名数组', () => {
    expect(cn('base', ['a', 'b'])).toBe('base a b');
  });

  it('tailwind-merge 解决冲突类名（后者覆盖前者）', () => {
    expect(cn('px-2', 'px-4')).toBe('px-4');
  });

  it('tailwind-merge 保留非冲突类名', () => {
    expect(cn('px-2 py-1', 'px-4')).toBe('py-1 px-4');
  });

  it('无参数返回空字符串', () => {
    expect(cn()).toBe('');
  });
});
