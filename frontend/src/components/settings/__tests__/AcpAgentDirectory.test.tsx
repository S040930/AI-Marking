import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  installMutate: vi.fn(),
  testMutate: vi.fn(),
  refreshMutate: vi.fn(),
  upsertMutate: vi.fn(),
  removeMutate: vi.fn(),
  uninstallMutate: vi.fn(),
  resetMutate: vi.fn(),
  whitelistData: {
    value: { rules: [] as { agent_id: string; source: string }[], has_overrides: false },
  },
}));

vi.mock('@/api/acp', () => ({
  useAcpAgents: () => ({
    data: {
      agents: [
        {
          agent_id: 'codex-acp',
          display_name: 'codex-acp',
          whitelist_package: '@zed-industries/codex-acp',
          distributions: ['npm', 'binary'],
          installed_version: '0.9.0',
          available_version: '0.9.1',
          connection_status: 'ready',
        },
      ],
      registry_version: 'registry-2026.09',
      fetched_at: null,
    },
    isLoading: false,
  }),
  useAcpWhitelist: () => ({ data: mocks.whitelistData.value }),
  useInstallAcpAgent: () => ({ mutate: mocks.installMutate, isPending: false }),
  useTestAcpAgent: () => ({ mutate: mocks.testMutate, isPending: false }),
  useRefreshAcpRegistry: () => ({
    mutate: mocks.refreshMutate,
    isPending: false,
  }),
  useUpsertAcpWhitelistAgent: () => ({
    mutate: mocks.upsertMutate,
    isPending: false,
  }),
  useRemoveAcpWhitelistAgent: () => ({
    mutate: mocks.removeMutate,
    isPending: false,
  }),
  useUninstallAcpAgent: () => ({
    mutate: mocks.uninstallMutate,
    isPending: false,
  }),
  useResetAcpWhitelist: () => ({
    mutate: mocks.resetMutate,
    isPending: false,
  }),
}));

import { AcpAgentDirectory } from '@/components/settings/AcpAgentDirectory';
import { LanguageProvider } from '@/i18n';

function renderWithProviders(ui: React.ReactElement) {
  return render(<LanguageProvider>{ui}</LanguageProvider>);
}

describe('AcpAgentDirectory', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.setItem('ai-marking-locale', 'zh-CN');
    mocks.whitelistData.value = { rules: [], has_overrides: false };
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });

  it('只渲染 Codex ACP 与安装状态', () => {
    renderWithProviders(<AcpAgentDirectory />);

    expect(screen.getByText('codex-acp')).toBeInTheDocument();
    expect(screen.queryByText('gemini')).not.toBeInTheDocument();
    expect(screen.queryByText('claude-acp')).not.toBeInTheDocument();
    expect(screen.getByText('Registry 版本: registry-2026.09')).toBeInTheDocument();
  });

  it('已安装 Codex 显示原生沙箱说明与连接测试,可更新时显示更新按钮', () => {
    renderWithProviders(<AcpAgentDirectory />);

    expect(screen.getByText('Codex 原生工作区沙箱，网络已关闭')).toBeInTheDocument();
    expect(screen.getByText(/可更新到 0.9.1/)).toBeInTheDocument();
  });

  it('点击安装按钮触发安装,点击测试连接触发诊断', () => {
    renderWithProviders(<AcpAgentDirectory />);

    fireEvent.click(screen.getByRole('button', { name: '更新' }));
    expect(mocks.installMutate).toHaveBeenCalledWith(
      'codex-acp',
      expect.anything(),
    );

    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
    expect(mocks.testMutate).toHaveBeenCalledWith(
      'codex-acp',
      expect.anything(),
    );
  });

  it('不提供默认助手或自定义白名单操作', () => {
    renderWithProviders(<AcpAgentDirectory />);

    expect(screen.queryByRole('button', { name: '设为默认' })).toBeNull();
    expect(screen.queryByRole('button', { name: '移出白名单' })).toBeNull();
  });

  // 注:自定义白名单(添加/移出/恢复默认)功能已按产品决策整体移除,
  // 后端接口返回 404(test_custom_whitelist_interfaces_are_removed),相关 UI 与用例一并删除。

  it('删除按钮触发卸载已安装链接,而不是移出白名单', () => {
    renderWithProviders(<AcpAgentDirectory />);

    fireEvent.click(screen.getByRole('button', { name: '卸载' }));
    expect(window.confirm).toHaveBeenCalled();
    expect(mocks.uninstallMutate).toHaveBeenCalledWith('codex-acp', expect.anything());
    expect(mocks.removeMutate).not.toHaveBeenCalled();
  });

  it('已安装 Codex 的卸载按钮可用', () => {
    renderWithProviders(<AcpAgentDirectory />);

    expect(screen.getByRole('button', { name: '卸载' })).toBeEnabled();
  });

  it('移出白名单相关 UI 不再出现(功能已移除)', () => {
    renderWithProviders(<AcpAgentDirectory />);
    expect(screen.queryByRole('button', { name: '移出白名单' })).toBeNull();
    expect(screen.queryByRole('button', { name: '恢复默认' })).toBeNull();
  });
});
