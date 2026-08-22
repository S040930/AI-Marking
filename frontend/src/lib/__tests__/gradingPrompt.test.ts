import { describe, expect, it } from 'vitest';
import { buildGradingPrompt } from '@/lib/gradingPrompt';

describe('buildGradingPrompt', () => {
  const input = {
    questionName: '实验一 数据分析',
    questionId: 'DTS208TC_CW1_Paper',
  };

  it.each(['zh-CN', 'en-US'] as const)(
    'embeds question name and id for %s',
    (locale) => {
      const prompt = buildGradingPrompt(input, locale);
      expect(prompt).toContain('实验一 数据分析');
      expect(prompt).toContain('DTS208TC_CW1_Paper');
    },
  );

  it('references the ai-marking-grader skill for the full workflow', () => {
    const prompt = buildGradingPrompt(input, 'zh-CN');
    expect(prompt).toContain('ai-marking-grader');
    expect(prompt).toContain('zip');
    expect(prompt).toContain('最终成绩由教师在网页确认');
  });

  it('does not inline the detailed grading workflow or policy', () => {
    const prompt = buildGradingPrompt(input, 'zh-CN');
    // 详细流程与评分标准全部交由 ai-marking-grader skill 提供，提示词本身不内嵌
    expect(prompt).not.toContain('open_ai_marking_assignment');
    expect(prompt).not.toContain('grade_assignment');
    expect(prompt).not.toContain('SKILL.md');
    expect(prompt).not.toContain('--- question ---');
    expect(prompt).not.toContain('grading_policy');
    expect(prompt).not.toContain('save_ai_marking_assessment');
  });

  it('replaces placeholders for different inputs', () => {
    const first = buildGradingPrompt(input, 'zh-CN');
    const second = buildGradingPrompt(
      { questionName: '实验二', questionId: 'DTS208TC_CW2_Paper' },
      'zh-CN',
    );
    expect(first).not.toBe(second);
    expect(second).toContain('实验二');
    expect(second).toContain('DTS208TC_CW2_Paper');
    expect(first).not.toContain('实验二');
  });

  it('renders distinct templates per locale', () => {
    const zh = buildGradingPrompt(input, 'zh-CN');
    const en = buildGradingPrompt(input, 'en-US');
    expect(zh).not.toBe(en);
    expect(zh).toContain('使用 ai-marking-grader skill 批改学生作业');
    expect(en).toContain("Grade one student's assignment with the ai-marking-grader skill");
  });
});
