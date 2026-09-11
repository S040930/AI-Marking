import { useEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import {
  History,
  Loader2,
  MessageSquarePlus,
  MoreHorizontal,
  Trash2,
  X,
} from 'lucide-react';
import {
  findPendingPermission,
  useAcpChatSession,
  useAcpChatSessions,
  useAcpChatEvents,
  useCancelChatTurn,
  useChatPermission,
  useCloseChatSession,
  useCreateAcpChatSession,
  useDeleteChatSessionPermanent,
  useSendChatMessage,
  useUpdateChatConfiguration,
  type AcpChatSession,
  type AcpChatSessionList,
  type CodexSelection,
} from '@/api/acpChat';
import {
  isTerminalRun,
  selectionToRequest,
  useActiveAcpRun,
  useAcpRunEvents,
  useCodexConfiguration,
  type AcpConfigSnapshot,
  type AcpPermissionMode,
} from '@/api/acp';
import { AcpRunProgress } from '@/components/review/AcpRunProgress';
import { Button } from '@/components/ui/button';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { ChatComposer } from './ChatComposer';
import { ChatDraftToolbar } from './ChatDraftToolbar';
import { ChatTranscript } from './ChatTranscript';
import { PermissionCard } from './PermissionCard';
import { queryKeys } from '@/api/queryKeys';
import { errorMessage } from '@/lib/questionErrors';
import { useLanguage } from '@/i18n';

const STATUS_LABEL: Record<string, string> = {
  idle: '空闲',
  running: '回复中',
  waiting_permission: '等待批准',
  closed: '已关闭',
  error: '异常',
};

function statusTone(status: AcpChatSession['status']): string {
  switch (status) {
    case 'running':
      return 'bg-primary/10 text-primary';
    case 'waiting_permission':
      return 'bg-warning/15 text-warning-foreground';
    case 'closed':
    case 'error':
      return 'bg-muted text-muted-foreground';
    default:
      return 'bg-success/10 text-success-foreground';
  }
}

/** 头部小圆点状态指示:一眼区分空闲/回复中/等待批准/已结束。 */
function StatusDot({ status }: { status: AcpChatSession['status'] | undefined }) {
  return (
    <span
      aria-hidden
      className={
        'size-2 shrink-0 rounded-full ' +
        (status === 'running'
          ? 'bg-primary animate-pulse'
          : status === 'waiting_permission'
            ? 'bg-warning'
            : status === 'closed' || status === 'error'
              ? 'bg-muted-foreground/40'
              : 'bg-success')
      }
    />
  );
}

function configSummary(
  snapshot: AcpConfigSnapshot | undefined,
  defaultModel: string,
  defaultReasoning: string,
  t: (value: string) => string,
) {
  return (
    (snapshot?.model_id ?? defaultModel) +
    ' · ' +
    (snapshot?.reasoning_effort ?? defaultReasoning) +
    ' · ' +
    (snapshot?.speed_mode === 'fast' ? t('快速') : t('标准'))
  );
}

function snapshotToSelection(snapshot: AcpConfigSnapshot | undefined): CodexSelection {
  return {
    modelId: snapshot?.model_id ?? null,
    reasoningEffort: snapshot?.reasoning_effort ?? null,
    speedMode: snapshot?.speed_mode === 'fast' ? 'fast' : 'standard',
  };
}

/** ReviewPage 右侧对话面板:Codex ACP 的作业上下文多轮对话。 */
export function AcpChatPanel({
  submissionId,
  canChat,
  prefill,
  onCollapse,
}: {
  submissionId: number;
  canChat: boolean;
  /** 外部入口(中间栏「开始批改」)下发的批改指令:填进输入框,由教师确认后手动发送。 */
  prefill?: { text: string; token: number } | null;
  onCollapse?: () => void;
}) {
  const { t } = useLanguage();
  const queryClient = useQueryClient();
  const [activeChatId, setActiveChatId] = useState<number | undefined>(undefined);
  const [composerText, setComposerText] = useState('');
  const [drafting, setDrafting] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [deleteTargetId, setDeleteTargetId] = useState<number | undefined>(undefined);
  const [draftPermissionMode, setDraftPermissionMode] = useState<AcpPermissionMode>('ask');
  const [draftSelection, setDraftSelection] = useState<CodexSelection>({
    modelId: null,
    reasoningEffort: null,
    speedMode: 'standard',
  });
  // 已有会话的待应用选择:与草稿态共用同一组下拉;切换即 PATCH,失败回滚。
  const [liveSelection, setLiveSelection] = useState<CodexSelection | null>(null);
  const [livePermissionMode, setLivePermissionMode] = useState<AcpPermissionMode | null>(null);

  const { data: sessionsData } = useAcpChatSessions(submissionId);
  const sessions = useMemo(() => sessionsData?.sessions ?? [], [sessionsData]);
  const {
    data: activeChat,
    isError: chatIsError,
    error: chatError,
    refetch: refetchChat,
  } = useAcpChatSession(activeChatId);
  const status = activeChat?.status;
  const terminal = status === 'closed' || status === 'error';
  const busy = status === 'running' || status === 'waiting_permission';
  const { data: catalog, isLoading: catalogLoading, isFetching: catalogFetching } = useCodexConfiguration(
    (activeChatId !== undefined ? liveSelection?.modelId : draftSelection.modelId) ?? null,
    canChat,
  );

  const { events } = useAcpChatEvents(activeChatId, activeChatId !== undefined);
  const pendingPermission = useMemo(() => findPendingPermission(events), [events]);

  // 批改运行:与中间栏共享同一份 run 查询。运行期间冻结对话,避免与 run
  // 争用 MCP 写工具(后端 send/messages 也会拒绝)。
  const {
    data: runDetail,
    isError: runIsError,
    error: runError,
  } = useActiveAcpRun(submissionId);
  // run 终态后 by-submission 返回 404,query 进入 error 状态但 data 停留旧值。
  const noActiveRun =
    runIsError && (runError as { response?: { status?: number } })?.response?.status === 404;
  const activeRun = noActiveRun ? undefined : runDetail?.run;
  const runningRun =
    activeRun !== undefined && !isTerminalRun(activeRun.status) ? activeRun : undefined;
  const runEvents = useAcpRunEvents(runningRun?.id, runningRun !== undefined);

  const createMutation = useCreateAcpChatSession(submissionId);
  const sendMutation = useSendChatMessage(activeChatId);
  const permissionMutation = useChatPermission(activeChatId);
  const cancelMutation = useCancelChatTurn(activeChatId);
  const closeMutation = useCloseChatSession(submissionId);
  const deleteMutation = useDeleteChatSessionPermanent();
  const updateConfigMutation = useUpdateChatConfiguration(activeChatId);

  // 会话切换时,从服务端快照重置本地"待应用"选择。
  useEffect(() => {
    if (activeChatId === undefined) {
      setLiveSelection(null);
      setLivePermissionMode(null);
      return;
    }
    if (activeChat === undefined) return;
    if (activeChat.id !== activeChatId) return;
    setLiveSelection(snapshotToSelection(activeChat.codex_config));
    setLivePermissionMode(activeChat.permission_mode);
  }, [activeChatId, activeChat]);

  useEffect(() => {
    if (activeChatId === undefined && sessions.length > 0 && !drafting) {
      setActiveChatId(sessions[0].id);
    } else if (
      activeChatId === undefined &&
      !drafting &&
      sessions.length === 0 &&
      sessionsData !== undefined
    ) {
      // 列表为空(如最后一个会话刚被删除):基于 refetch 后的新数据回到草稿态
      // (与 backToDraft 等效;此处内联以保持依赖数组最小)
      setDrafting(true);
      setMenuOpen(false);
      draftDefaultsApplied.current = false;
    }
  }, [sessions, sessionsData, activeChatId, drafting]);

  // 会话查询 404(已删除/不存在)时解除选中,交还 auto-select effect 用
  // 新列表重选;其他错误交给渲染层的错误态。防御层:即使删除流程已正确
  // 换选,任何残留的失效 activeChatId 也不会造成永久 spinner。
  useEffect(() => {
    if (!chatIsError || activeChatId === undefined) return;
    const errorStatus = (chatError as { response?: { status?: number } } | null)
      ?.response?.status;
    if (errorStatus === 404) {
      setActiveChatId(undefined);
      setDrafting(false);
    }
  }, [chatIsError, chatError, activeChatId]);

  const draftDefaultsApplied = useRef(false);
  useEffect(() => {
    // showDraft 涵盖初始态(activeChatId 未定且未显式点"新对话"),同样需要默认值;
    // 只灌一次:用户手动改过下拉后不再覆盖(模型切换刷新档位走 query key 变化)。
    if (!catalog || !canChat || activeChatId !== undefined) return;
    if (draftDefaultsApplied.current) return;
    draftDefaultsApplied.current = true;
    setDraftSelection((current) => ({
      modelId: current.modelId ?? catalog.selected_model_id ?? catalog.models[0]?.id ?? null,
      reasoningEffort:
        current.reasoningEffort ??
        catalog.reasoning_efforts.find((option) => option.current)?.id ??
        catalog.reasoning_efforts[0]?.id ??
        null,
      speedMode: current.speedMode,
    }));
  }, [catalog, canChat, activeChatId]);

  // 外部入口下发的批改指令:token 变化即覆盖输入框,教师随后可自由编辑。
  // 面板被窄屏自动收起、拉宽后重新挂载时也会应用一次(草稿存于父级)。
  const prefillToken = prefill?.token;
  const prefillText = prefill?.text;
  useEffect(() => {
    if (prefillText === undefined) return;
    setComposerText(prefillText);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefillToken]);

  const pendingFirstMessage = useRef<string | null>(null);
  useEffect(() => {
    if (pendingFirstMessage.current === null || activeChatId === undefined) return;
    const text = pendingFirstMessage.current;
    pendingFirstMessage.current = null;
    sendMutation.mutate(text, {
      onError: (err) => toast.error(err.message),
    });
    // sendMutation follows activeChatId; this effect runs when the new session is selected.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeChatId]);

  const startChat = (firstText?: string) => {
    pendingFirstMessage.current = firstText ?? null;
    createMutation.mutate(
      {
        permission_mode: draftPermissionMode,
        codex_config: selectionToRequest(draftSelection),
      },
      {
        onSuccess: (chat) => {
          setActiveChatId(chat.id);
          setDrafting(false);
          draftDefaultsApplied.current = false;
          toast.success(t('对话会话已创建'));
        },
        onError: (err) => {
          pendingFirstMessage.current = null;
          toast.error(err.message);
        },
      },
    );
  };

  const handleSend = (text: string) => {
    if (activeChatId === undefined || drafting) {
      startChat(text);
      return;
    }
    sendMutation.mutate(text, {
      onError: (err) => toast.error(err.message),
    });
  };

  /** 已有会话:模型/思考度/速度或权限档位变化 → PATCH 热更新,失败回滚。 */
  const updateLiveConfig = (
    next: { selection?: CodexSelection; permissionMode?: AcpPermissionMode },
    previous: { selection: CodexSelection; permissionMode: AcpPermissionMode },
  ) => {
    if (activeChatId === undefined) return;
    if (next.selection) setLiveSelection(next.selection);
    if (next.permissionMode) setLivePermissionMode(next.permissionMode);
    updateConfigMutation.mutate(
      {
        ...(next.selection ? { codex_config: selectionToRequest(next.selection) } : {}),
        ...(next.permissionMode ? { permission_mode: next.permissionMode } : {}),
      },
      {
        onError: (err) => {
          setLiveSelection(previous.selection);
          setLivePermissionMode(previous.permissionMode);
          toast.error(err.message);
        },
      },
    );
  };

  const backToDraft = () => {
    setDrafting(true);
    setActiveChatId(undefined);
    setMenuOpen(false);
    draftDefaultsApplied.current = false;
  };

  const handleDeleteConversation = (chatId: number) => {
    deleteMutation.mutate(chatId, {
      onSuccess: () => {
        toast.success(t('对话已永久删除'));
        setDeleteTargetId(undefined);
        // 先从缓存移除被删会话再解除选中:auto-select effect 不会拿删除前
        // 的旧列表重选已删会话(旧实现基于旧 sessions 推算 remaining,
        // refetch 未落地时会选中已删除的会话 → 404 → 永久 spinner)。
        queryClient.setQueryData<AcpChatSessionList>(
          queryKeys.acp.chat.sessions(submissionId),
          (old) =>
            old
              ? { sessions: old.sessions.filter((chat) => chat.id !== chatId) }
              : old,
        );
        if (chatId !== activeChatId) return;
        setActiveChatId(undefined);
        setDrafting(false);
      },
      onError: (err) => {
        setDeleteTargetId(undefined);
        toast.error(err.message || t('删除对话失败'));
      },
    });
  };

  const showDraft = canChat && (activeChatId === undefined || drafting);
  // 会话加载失败(非 404;404 已由上方 effect 解除选中)→ 显示错误态而非永久 spinner
  const chatLoadFailed =
    chatIsError &&
    activeChatId !== undefined &&
    activeChat === undefined &&
    (chatError as { response?: { status?: number } } | null)?.response?.status !== 404;

  return (
    <div className="flex h-full w-full flex-col bg-white">
      <div className="flex h-12 shrink-0 items-center gap-1.5 border-b border-border/60 px-3">
        <StatusDot status={activeChatId !== undefined && !drafting ? status : undefined} />
        <span className="min-w-0 flex-1 truncate text-sm font-semibold">{t('Codex ACP')}</span>
        {activeChatId !== undefined && !drafting && canChat ? (
          <Button
            size="sm"
            variant="ghost"
            className="h-8 px-2 text-xs text-muted-foreground"
            onClick={backToDraft}
          >
            <MessageSquarePlus className="size-4" />
            {t('新对话')}
          </Button>
        ) : null}
        {sessions.length > 0 ? (
          <Popover open={historyOpen} onOpenChange={setHistoryOpen}>
            <PopoverTrigger asChild>
              <Button size="icon" variant="ghost" className="size-8" aria-label={t('会话历史')}>
                <History className="size-4" />
              </Button>
            </PopoverTrigger>
            <PopoverContent align="end" className="w-80 p-2">
              <p className="px-2 pb-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                {t('会话历史')}
              </p>
              <ul className="space-y-0.5">
                {sessions.map((chat) => (
                  <li key={chat.id} className="group relative">
                    <button
                      type="button"
                      className={
                        'flex w-full items-center gap-2 rounded-lg py-1.5 pl-2 pr-8 text-left text-xs hover:bg-muted ' +
                        (chat.id === activeChatId && !drafting ? 'bg-muted' : '')
                      }
                      onClick={() => {
                        setDrafting(false);
                        setActiveChatId(chat.id);
                        setHistoryOpen(false);
                      }}
                    >
                      <span className="text-muted-foreground">#{chat.id}</span>
                      <span className="min-w-0 flex-1 truncate">
                        {configSummary(chat.codex_config, t('Agent 默认模型'), t('Agent 默认思考'), t)}
                      </span>
                      <span className={'rounded-full px-1.5 py-0.5 text-[10px] ' + statusTone(chat.status)}>
                        {t(STATUS_LABEL[chat.status] ?? chat.status)}
                      </span>
                    </button>
                    <button
                      type="button"
                      aria-label={t('删除对话') + ' #' + chat.id}
                      title={t('删除对话')}
                      className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded-md p-1 text-muted-foreground opacity-0 transition-opacity hover:bg-destructive/10 hover:text-destructive focus-visible:opacity-100 focus-visible:outline-none group-hover:opacity-100"
                      onClick={(event) => {
                        event.stopPropagation();
                        setHistoryOpen(false);
                        setDeleteTargetId(chat.id);
                      }}
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  </li>
                ))}
              </ul>
            </PopoverContent>
          </Popover>
        ) : null}
        {activeChatId !== undefined && !drafting ? (
          <Popover open={menuOpen} onOpenChange={setMenuOpen}>
            <PopoverTrigger asChild>
              <Button size="icon" variant="ghost" className="size-8" aria-label={t('更多操作')}>
                <MoreHorizontal className="size-4" />
              </Button>
            </PopoverTrigger>
            <PopoverContent align="end" className="w-44 p-1">
              <ul>
                {!terminal ? (
                  <li>
                    <button
                      type="button"
                      className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs hover:bg-muted"
                      onClick={() => {
                        setMenuOpen(false);
                        closeMutation.mutate(activeChat.id, {
                          onSuccess: () => {
                            toast.success(t('已关闭会话'));
                            setActiveChatId(undefined);
                            setDrafting(true);
                          },
                          onError: (err) => toast.error(err.message),
                        });
                      }}
                      disabled={closeMutation.isPending}
                    >
                      <X className="size-3.5 text-muted-foreground" />
                      {t('关闭会话')}
                    </button>
                  </li>
                ) : null}
                <li>
                  <button
                    type="button"
                    className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs text-destructive hover:bg-destructive/10"
                    onClick={() => {
                      setMenuOpen(false);
                      setDeleteTargetId(activeChatId);
                    }}
                  >
                    <Trash2 className="size-3.5" />
                    {t('删除对话')}
                  </button>
                </li>
              </ul>
            </PopoverContent>
          </Popover>
        ) : null}
        {onCollapse ? (
          <Button size="icon" variant="ghost" className="size-8" aria-label={t('收起对话面板')} onClick={onCollapse}>
            <X className="size-4" />
          </Button>
        ) : null}
      </div>

      {runningRun !== undefined ? (
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-3">
          <AcpRunProgress run={runningRun} events={runEvents} showTranscript={false} />
          <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">
            {t('批改运行进行中,对话已暂时冻结。')}
          </p>
        </div>
      ) : showDraft ? (
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
          {catalogLoading ? (
            <div className="flex items-center gap-2 border-b border-border/60 px-4 py-2 text-xs text-muted-foreground">
              <Loader2 className="size-3.5 animate-spin" /> {t('读取 Codex 配置能力中…')}
            </div>
          ) : catalog ? null : (
            <p className="shrink-0 bg-amber-50 px-4 py-2 text-xs text-amber-700">
              {t('请先在系统设置安装并连接测试 Codex ACP。')}
            </p>
          )}
          <ChatComposer
            variant="full"
            value={composerText}
            onChange={setComposerText}
            disabled={!canChat || createMutation.isPending}
            isSending={createMutation.isPending}
            onSend={handleSend}
            placeholder={t('随心输入')}
            toolbarControls={
              catalog ? (
                <ChatDraftToolbar
                  catalog={catalog}
                  catalogFetching={catalogFetching}
                  permissionMode={draftPermissionMode}
                  selection={draftSelection}
                  disabled={createMutation.isPending}
                  onPermissionModeChange={setDraftPermissionMode}
                  onSelectionChange={setDraftSelection}
                />
              ) : null
            }
          />
        </div>
      ) : chatLoadFailed ? (
        <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
          <p className="text-sm text-muted-foreground">
            {t('会话加载失败')}
            {chatError?.message ? `：${errorMessage(chatError)}` : ''}
          </p>
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              void refetchChat();
            }}
          >
            {t('重试')}
          </Button>
        </div>
      ) : activeChat === undefined ? (
        <div className="flex flex-1 items-center justify-center">
          <Loader2 className="size-6 animate-spin text-primary" />
        </div>
      ) : (
        <>
          {!canChat ? (
            <p className="shrink-0 bg-amber-50 px-4 py-2 text-xs text-amber-700">
              {t('批改流程进行中,暂不可继续对话。')}
            </p>
          ) : null}
          {activeChat.last_error ? (
            <p className="shrink-0 bg-destructive/8 px-4 py-2 text-xs text-destructive">
              {activeChat.last_error}
            </p>
          ) : null}

          <ChatTranscript events={events} />
          {status === 'waiting_permission' && pendingPermission ? (
            <PermissionCard request={pendingPermission} mutation={permissionMutation} />
          ) : null}
          {!terminal ? (
            <ChatComposer
              variant="docked"
              value={composerText}
              onChange={setComposerText}
              disabled={!canChat}
              running={busy}
              isSending={sendMutation.isPending}
              onSend={handleSend}
              onStop={() =>
                cancelMutation.mutate(undefined, {
                  onError: (err) => toast.error(err.message),
                })
              }
              toolbarControls={
                catalog && liveSelection ? (
                  <ChatDraftToolbar
                    catalog={catalog}
                    catalogFetching={catalogFetching}
                    permissionMode={livePermissionMode ?? activeChat.permission_mode}
                    selection={liveSelection}
                    disabled={updateConfigMutation.isPending}
                    onPermissionModeChange={(mode) =>
                      updateLiveConfig(
                        { permissionMode: mode },
                        {
                          selection: liveSelection,
                          permissionMode: livePermissionMode ?? activeChat.permission_mode,
                        },
                      )
                    }
                    onSelectionChange={(selection) =>
                      updateLiveConfig(
                        { selection },
                        {
                          selection: liveSelection,
                          permissionMode: livePermissionMode ?? activeChat.permission_mode,
                        },
                      )
                    }
                  />
                ) : null
              }
            />
          ) : null}
        </>
      )}

      <AlertDialog
        open={deleteTargetId !== undefined}
        onOpenChange={(open) => {
          if (!open) setDeleteTargetId(undefined);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t('删除对话')}</AlertDialogTitle>
            <AlertDialogDescription>
              {t('将永久删除该对话的聊天记录、事件转录与专属工作区，此操作不可恢复。')}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteMutation.isPending}>{t('取消')}</AlertDialogCancel>
            <AlertDialogAction
              disabled={deleteMutation.isPending}
              className="bg-destructive text-white hover:bg-destructive/90"
              onClick={(event) => {
                event.preventDefault();
                if (deleteTargetId !== undefined) handleDeleteConversation(deleteTargetId);
              }}
            >
              {deleteMutation.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
              {t('永久删除')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
