import { describe, expect, it } from 'vitest';
import {
  buildAcpChatGradingPrompt,
  buildGradingPrompt,
  buildMcpResumePrompt,
} from '@/lib/gradingPrompt';

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

describe('buildMcpResumePrompt', () => {
  const input = {
    questionName: 'DTS208TC_CW1_Paper',
    questionId: 'DTS208TC_CW1_Paper',
  };

  it('已存在作业:打开评分包继续,不点名模式', () => {
    const prompt = buildMcpResumePrompt(input, 'zh-CN');
    expect(prompt).toContain('已上传到 AI-Marking');
    expect(prompt).toContain('ai-marking-grader');
    expect(prompt).toContain('最终成绩由教师在网页确认');
    // 已存在作业不该提 zip,也不点名模式
    expect(prompt).not.toContain('zip');
    expect(prompt).not.toContain('MCP 模式');
    expect(prompt).not.toContain('ACP 模式');
  });

  it('提示词均不点名模式:模式由 SKILL.md 模式判定规则自检', () => {
    const zhMcp = buildGradingPrompt(input, 'zh-CN');
    const enMcp = buildGradingPrompt(input, 'en-US');
    const zhResume = buildMcpResumePrompt(input, 'zh-CN');
    const enResume = buildMcpResumePrompt(input, 'en-US');
    const zhAcp = buildAcpChatGradingPrompt(
      { questionName: input.questionName, submissionId: 42 },
      'zh-CN',
    );
    const enAcp = buildAcpChatGradingPrompt(
      { questionName: input.questionName, submissionId: 42 },
      'en-US',
    );
    for (const prompt of [zhMcp, enMcp, zhResume, enResume, zhAcp, enAcp]) {
      expect(prompt).not.toContain('MCP 模式');
      expect(prompt).not.toContain('ACP 模式');
      // 每条提示词都必须能引用到 ai-marking-grader skill 或工作区 skill.md
      expect(
        prompt.toLowerCase().includes('ai-marking-grader') || prompt.includes('skill.md'),
      ).toBe(true);
    }
    for (const prompt of [zhMcp, zhResume, zhAcp]) {
      expect(prompt).toContain('最终成绩由教师在网页确认');
    }
    for (const prompt of [enMcp, enResume, enAcp]) {
      const lower = prompt.toLowerCase();
      expect(lower).toContain('final grade');
      expect(lower).toContain('teacher');
      expect(lower).toContain('web page');
    }
  });
});

describe('buildAcpChatGradingPrompt', () => {
  const input = {
    questionName: 'DTS208TC_CW1_Paper',
    submissionId: 42,
  };

  it('只做入口:读工作区 skill.md 收敛完整流程,不点名模式', () => {
    const prompt = buildAcpChatGradingPrompt(input, 'zh-CN');
    expect(prompt).toContain('DTS208TC_CW1_Paper');
    expect(prompt).toContain('submission_id=42');
    expect(prompt).toContain('skill.md');
    // 程序流程/证据契约/模式判定全部收敛到 SKILL.md,提示词不点名模式
    expect(prompt).not.toContain('ACP 模式');
    expect(prompt).not.toContain('MCP 模式');
    expect(prompt).not.toContain('open_ai_marking_assignment');
    expect(prompt).not.toContain('source_line');
    expect(prompt).not.toContain('report_quote');
    expect(prompt).not.toContain('zip');
    // 已存在作业不得再走外部助手上传链路
    expect(prompt).toContain('请勿调用 prepare_ai_marking_submission');
    expect(prompt).toContain('最终成绩由教师在网页确认');
  });

  it.each(['zh-CN', 'en-US'] as const)(
    'embeds question name and submission id for %s',
    (locale) => {
      const prompt = buildAcpChatGradingPrompt(input, locale);
      expect(prompt).toContain('DTS208TC_CW1_Paper');
      expect(prompt).toContain('42');
      expect(prompt).not.toContain('{questionName}');
      expect(prompt).not.toContain('{submissionId}');
    },
  );
});
