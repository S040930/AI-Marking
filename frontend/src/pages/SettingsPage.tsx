import { useEffect, useMemo, useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { toast } from 'sonner';
import {
  Copy,
  Loader2,
  Plus,
  Save,
  Star,
  Trash2,
  Undo2,
  AlertCircle,
  Bot,
  ShieldCheck,
  ScanEye,
  ClipboardCheck,
  MessageSquareText,
  FolderKanban,
  Pencil,
} from 'lucide-react';
import {
  type ConfigOut,
  useConfig,
  useConfigProfiles,
  useCreateConfigProfile,
  useDeleteConfigProfile,
  useRenameConfigProfile,
  useSetDefaultConfigProfile,
  useUpdateConfig,
} from '@/api/config';
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Button } from '@/components/ui/button';
import { Separator } from '@/components/ui/separator';
import { Badge } from '@/components/ui/badge';
import {
  Alert,
  AlertDescription,
  AlertTitle,
} from '@/components/ui/alert';
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

const DEFAULT_RUBRIC_PLACEHOLDER = `请输入 JSON 结构，例如:
{
  "items": [
    {"criterion": "内容理解", "max_score": 60, "details": "准确理解题目要求"},
    {"criterion": "论证分析", "max_score": 40, "details": "论证清晰且有证据"}
  ],
  "total_max_score": 100
}`;

const configSchema = z
  .object({
    llm_api_key: z.string().optional(),
    llm_base_url: z.string().optional(),
    llm_model: z.string().optional(),
    review_llm_api_key: z.string().optional(),
    review_llm_base_url: z.string().optional(),
    review_llm_model: z.string().optional(),
    paddleocr_api_url: z.string().optional(),
    paddleocr_token: z.string().optional(),
    rubric_definition: z.string().optional(),
    llm_user_prompt: z.string().optional(),
  })
  .superRefine((data, ctx) => {
    // 审核 LLM「全有或全无」:三字段必须同时填写或同时留空
    const filled = [
      data.review_llm_api_key,
      data.review_llm_base_url,
      data.review_llm_model,
    ].filter(Boolean).length;
    if (filled !== 0 && filled !== 3) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message:
          '审核 LLM 的 API Key、Base URL、Model 必须同时填写或同时留空',
        path: ['review_llm_api_key'],
      });
    }
  });

type ConfigFormValues = z.infer<typeof configSchema>;

const defaultValues: ConfigFormValues = {
  llm_api_key: '',
  llm_base_url: '',
  llm_model: '',
  review_llm_api_key: '',
  review_llm_base_url: '',
  review_llm_model: '',
  paddleocr_api_url: '',
  paddleocr_token: '',
  rubric_definition: '',
  llm_user_prompt: '',
};

function errorText(error: Error): string {
  const e = error as { response?: { data?: { detail?: string } } };
  return e.response?.data?.detail ?? error.message ?? '操作失败';
}

interface ProfileDialogState {
  open: boolean;
  mode: 'create' | 'rename' | 'copy';
  profileId?: number;
  profileName?: string;
}

export default function SettingsPage() {
  const { data: profiles, isLoading: profilesLoading } = useConfigProfiles();
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [dialog, setDialog] = useState<ProfileDialogState>({
    open: false,
    mode: 'create',
  });
  const [confirmDelete, setConfirmDelete] = useState(false);

  const defaultProfile = useMemo(
    () => profiles?.find((p) => p.is_default) ?? null,
    [profiles],
  );
  const selectedProfile = useMemo(
    () => profiles?.find((p) => p.id === selectedId) ?? null,
    [profiles, selectedId],
  );
  const displayProfileId = selectedProfile?.id ?? defaultProfile?.id;

  // 打开页面时默认选中默认项目
  useEffect(() => {
    if (selectedId === null && defaultProfile) {
      setSelectedId(defaultProfile.id);
    }
  }, [defaultProfile, selectedId]);

  const { data, isLoading, isError, error } = useConfig(
    displayProfileId ?? undefined,
  );
  const updateMutation = useUpdateConfig(displayProfileId ?? undefined);
  const createMutation = useCreateConfigProfile();
  const renameMutation = useRenameConfigProfile();
  const deleteMutation = useDeleteConfigProfile();
  const setDefaultMutation = useSetDefaultConfigProfile();

  const form = useForm<ConfigFormValues>({
    resolver: zodResolver(configSchema),
    defaultValues,
  });

  useEffect(() => {
    if (data) {
      form.reset({
        llm_api_key: data.llm_api_key,
        llm_base_url: data.llm_base_url,
        llm_model: data.llm_model,
        review_llm_api_key: data.review_llm_api_key,
        review_llm_base_url: data.review_llm_base_url,
        review_llm_model: data.review_llm_model,
        paddleocr_api_url: data.paddleocr_api_url,
        paddleocr_token: data.paddleocr_token,
        rubric_definition: data.rubric_definition
          ? JSON.stringify(data.rubric_definition, null, 2)
          : '',
        llm_user_prompt: data.llm_user_prompt,
      });
    }
  }, [data, form]);

  const onSubmit = (values: ConfigFormValues) => {
    let rubric_definition: ConfigOut['rubric_definition'] = null;
    if (values.rubric_definition?.trim()) {
      try {
        rubric_definition = JSON.parse(values.rubric_definition) as ConfigOut['rubric_definition'];
      } catch {
        toast.error('Rubric 必须是合法 JSON');
        return;
      }
    }
    const { rubric_definition: _raw, ...rest } = values;
    updateMutation.mutate({ ...rest, rubric_definition }, {
      onSuccess: () => {
        toast.success('配置已保存');
      },
      onError: (err) => {
        toast.error(errorText(err));
      },
    });
  };

  const handleResetRubric = () => {
    form.setValue('rubric_definition', '', { shouldDirty: true });
    toast.info('Rubric 已清空，保存后将使用内置默认 rubric');
  };

  const openDialog = (mode: 'create' | 'rename' | 'copy') => {
    if (mode === 'rename' || mode === 'copy') {
      if (!selectedProfile) return;
      setDialog({
        open: true,
        mode,
        profileId: selectedProfile.id,
        profileName:
          mode === 'copy' ? `${selectedProfile.name}-副本` : selectedProfile.name,
      });
    } else {
      setDialog({ open: true, mode, profileName: '' });
    }
  };

  const handleDialogSubmit = (name: string) => {
    const trimmed = name.trim();
    if (!trimmed) {
      toast.error('请输入配置项目名称');
      return;
    }
    if (dialog.mode === 'create') {
      createMutation.mutate(
        { name: trimmed },
        {
          onSuccess: (created) => {
            toast.success('配置项目已创建');
            setDialog({ open: false, mode: 'create' });
            setSelectedId(created.id);
          },
          onError: (err) => toast.error(errorText(err)),
        },
      );
    } else if (dialog.mode === 'rename') {
      renameMutation.mutate(
        { id: dialog.profileId!, name: trimmed },
        {
          onSuccess: () => {
            toast.success('配置项目已重命名');
            setDialog({ open: false, mode: 'create' });
          },
          onError: (err) => toast.error(errorText(err)),
        },
      );
    } else if (dialog.mode === 'copy') {
      createMutation.mutate(
        { name: trimmed, copy_from_id: dialog.profileId },
        {
          onSuccess: (created) => {
            toast.success('已复制为新配置项目');
            setDialog({ open: false, mode: 'create' });
            setSelectedId(created.id);
          },
          onError: (err) => toast.error(errorText(err)),
        },
      );
    }
  };

  const handleDelete = () => {
    if (!selectedProfile || selectedProfile.is_default) return;
    deleteMutation.mutate(selectedProfile.id, {
      onSuccess: () => {
        toast.success('配置项目已删除');
        setSelectedId(defaultProfile?.id ?? null);
        setConfirmDelete(false);
      },
      onError: (err) => {
        toast.error(errorText(err));
        setConfirmDelete(false);
      },
    });
  };

  const handleSetDefault = () => {
    if (!selectedProfile || selectedProfile.is_default) return;
    setDefaultMutation.mutate(selectedProfile.id, {
      onSuccess: () => toast.success('已设为默认配置项目'),
      onError: (err) => toast.error(errorText(err)),
    });
  };

  if (isLoading || profilesLoading) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-32">
        <Loader2 className="size-8 animate-spin text-primary" />
        <p className="text-sm text-muted-foreground">加载配置中...</p>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="mx-auto max-w-2xl">
        <Card className="elevated-card border-0">
          <CardContent className="pt-6">
            <Alert variant="destructive">
              <AlertCircle />
              <AlertTitle>加载配置失败</AlertTitle>
              <AlertDescription>{error?.message}</AlertDescription>
            </Alert>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl">
      <div className="mb-6">
        <h1 className="text-2xl font-bold tracking-tight text-foreground">
          系统设置
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          配置批改大模型、审核大模型、OCR 解析与评分标准，支持多套独立配置项目
        </p>
      </div>

      {/* 配置项目管理 */}
      <Card className="elevated-card mb-5">
        <CardHeader className="pb-3">
          <div className="flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-xl bg-gradient-to-br from-primary/10 to-primary/5 text-primary ring-1 ring-primary/10">
              <FolderKanban className="size-5" />
            </div>
            <div>
              <CardTitle className="text-lg">配置项目</CardTitle>
              <CardDescription>
                每套配置独立管理，题目在上传时选择使用哪一套
              </CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-3 pt-1">
          <div className="flex flex-wrap gap-2">
            {profiles?.map((p) => (
              <Button
                key={p.id}
                type="button"
                variant={displayProfileId === p.id ? 'default' : 'outline'}
                size="sm"
                onClick={() => setSelectedId(p.id)}
                className="gap-1.5"
              >
                {p.is_default && <Star className="size-3.5" />}
                {p.name}
              </Button>
            ))}
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => openDialog('create')}
              className="gap-1.5 text-muted-foreground"
            >
              <Plus className="size-4" />
              新建配置项目
            </Button>
          </div>

          {selectedProfile && (
            <div className="flex flex-wrap items-center gap-2 rounded-xl border border-border bg-muted/40 px-3 py-2">
              <span className="text-sm font-medium text-foreground">
                {selectedProfile.name}
              </span>
              {selectedProfile.is_default && (
                <Badge variant="secondary" className="gap-1">
                  <Star className="size-3" />
                  默认
                </Badge>
              )}
              <div className="ml-auto flex flex-wrap gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => openDialog('rename')}
                  className="gap-1.5"
                >
                  <Pencil className="size-3.5" />
                  重命名
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => openDialog('copy')}
                  className="gap-1.5"
                >
                  <Copy className="size-3.5" />
                  复制
                </Button>
                {!selectedProfile.is_default && (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={handleSetDefault}
                    className="gap-1.5"
                  >
                    <Star className="size-3.5" />
                    设为默认
                  </Button>
                )}
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setConfirmDelete(true)}
                  disabled={selectedProfile.is_default}
                  className="gap-1.5 text-destructive hover:bg-destructive/10 hover:text-destructive"
                >
                  <Trash2 className="size-3.5" />
                  删除
                </Button>
              </div>
            </div>
          )}
          {profiles && profiles.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              暂无配置项目，点击「新建配置项目」创建第一套配置
            </p>
          ) : null}
        </CardContent>
      </Card>

      {!selectedProfile ? (
        <Card className="elevated-card">
          <CardContent className="pt-6">
            <Alert>
              <AlertTitle>请选择一个配置项目</AlertTitle>
              <AlertDescription>
                选择或新建配置项目后可编辑其详细配置
              </AlertDescription>
            </Alert>
          </CardContent>
        </Card>
      ) : (
        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(onSubmit)}
            className="flex flex-col gap-5"
          >
            <Card className="elevated-card stagger-1 animate-fade-in-up motion-reduce:animate-none overflow-hidden">
              <CardHeader className="pb-4">
                <div className="flex items-center gap-3">
                  <div className="flex size-10 items-center justify-center rounded-xl bg-gradient-to-br from-primary/10 to-primary/5 text-primary ring-1 ring-primary/10">
                    <Bot className="size-5" />
                  </div>
                  <div>
                    <CardTitle className="text-lg">LLM 大模型配置</CardTitle>
                    <CardDescription>
                      支持 OpenAI 兼容协议服务：豆包、通义千问、DeepSeek、OpenAI、Kimi 等
                    </CardDescription>
                  </div>
                </div>
              </CardHeader>
              <CardContent className="flex flex-col gap-4 pt-5">
                <FormField
                  control={form.control}
                  name="llm_api_key"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>API Key</FormLabel>
                      <FormControl>
                        <Input
                          type="password"
                          placeholder="请输入 API Key"
                          autoComplete="new-password"
                          {...field}
                        />
                      </FormControl>
                      <FormDescription>对应服务的 API Key</FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="llm_base_url"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Base URL</FormLabel>
                      <FormControl>
                        <Input
                          placeholder="https://ark.cn-beijing.volces.com/api/v3"
                          {...field}
                        />
                      </FormControl>
                      <FormDescription>
                        留空使用默认 https://ark.cn-beijing.volces.com/api/v3（豆包）
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="llm_model"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Model / Endpoint ID</FormLabel>
                      <FormControl>
                        <Input placeholder="doubao-pro-32k" {...field} />
                      </FormControl>
                      <FormDescription>留空使用默认 doubao-pro-32k</FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </CardContent>
            </Card>

            <Card className="elevated-card stagger-2 animate-fade-in-up motion-reduce:animate-none overflow-hidden">
              <CardHeader className="pb-4">
                <div className="flex items-center gap-3">
                  <div className="flex size-10 items-center justify-center rounded-xl bg-gradient-to-br from-primary/10 to-primary/5 text-primary ring-1 ring-primary/10">
                    <ShieldCheck className="size-5" />
                  </div>
                  <div>
                    <CardTitle className="text-lg">审核 LLM 配置</CardTitle>
                    <CardDescription>
                      用于 critic 独立复核节点，与批改 LLM 独立配置
                    </CardDescription>
                  </div>
                </div>
              </CardHeader>
              <CardContent className="flex flex-col gap-4 pt-5">
                <FormField
                  control={form.control}
                  name="review_llm_api_key"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>API Key</FormLabel>
                      <FormControl>
                        <Input
                          type="password"
                          placeholder="请输入审核 LLM 的 API Key"
                          autoComplete="new-password"
                          {...field}
                        />
                      </FormControl>
                      <FormDescription>审核用 LLM 服务的 API Key</FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="review_llm_base_url"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Base URL</FormLabel>
                      <FormControl>
                        <Input
                          placeholder="https://ark.cn-beijing.volces.com/api/v3"
                          {...field}
                        />
                      </FormControl>
                      <FormDescription>
                        留空使用默认 https://ark.cn-beijing.volces.com/api/v3（豆包）
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="review_llm_model"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Model / Endpoint ID</FormLabel>
                      <FormControl>
                        <Input placeholder="doubao-pro-32k" {...field} />
                      </FormControl>
                      <FormDescription>
                        三字段需同时填写或同时留空；留空时复核将降级为人工审核
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </CardContent>
            </Card>

            <Card className="elevated-card stagger-3 animate-fade-in-up motion-reduce:animate-none overflow-hidden">
              <CardHeader className="pb-4">
                <div className="flex items-center gap-3">
                  <div className="flex size-10 items-center justify-center rounded-xl bg-gradient-to-br from-primary/10 to-primary/5 text-primary ring-1 ring-primary/10">
                    <ScanEye className="size-5" />
                  </div>
                  <div>
                    <CardTitle className="text-lg">PaddleOCR-VL 文档解析</CardTitle>
                    <CardDescription>
                      将 PDF 解析为结构化 Markdown，保留表格、公式与阅读顺序
                    </CardDescription>
                  </div>
                </div>
              </CardHeader>
              <CardContent className="flex flex-col gap-4 pt-5">
                <FormField
                  control={form.control}
                  name="paddleocr_api_url"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>PaddleOCR API URL</FormLabel>
                      <FormControl>
                        <Input
                          placeholder="https://xxx.aistudio.baidu.com/xxx"
                          {...field}
                        />
                      </FormControl>
                      <FormDescription>
                        请填写完整接口：异步任务入口 .../api/v2/ocr/jobs，或同步入口 .../layout-parsing；不要填写 .../api/v2/ocr/jobs/layout-parsing。
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="paddleocr_token"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>PaddleOCR Access Token</FormLabel>
                      <FormControl>
                        <Input
                          type="password"
                          placeholder="请输入 PaddleOCR Access Token"
                          autoComplete="new-password"
                          {...field}
                        />
                      </FormControl>
                      <FormDescription>
                        AI Studio 个人访问令牌
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </CardContent>
            </Card>

            <Card className="elevated-card stagger-4 animate-fade-in-up motion-reduce:animate-none overflow-hidden">
              <CardHeader className="pb-4">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex items-center gap-3">
                    <div className="flex size-10 items-center justify-center rounded-xl bg-gradient-to-br from-primary/10 to-primary/5 text-primary ring-1 ring-primary/10">
                      <ClipboardCheck className="size-5" />
                    </div>
                    <div>
                      <CardTitle className="text-lg">评分标准(Rubric)</CardTitle>
                      <CardDescription>
                        使用结构化评分项；留空使用内置默认 rubric
                      </CardDescription>
                    </div>
                  </div>
                  <CardAction>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={handleResetRubric}
                    >
                      <Undo2 className="size-4" />
                      重置为默认
                    </Button>
                  </CardAction>
                </div>
              </CardHeader>
              <CardContent className="pt-5">
                <FormField
                  control={form.control}
                  name="rubric_definition"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>结构化 Rubric JSON</FormLabel>
                      <FormControl>
                        <Textarea
                          rows={10}
                          placeholder={DEFAULT_RUBRIC_PLACEHOLDER}
                          className="font-mono text-sm"
                          {...field}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </CardContent>
            </Card>

            <Card className="elevated-card stagger-4 animate-fade-in-up motion-reduce:animate-none overflow-hidden">
              <CardHeader className="pb-4">
                <div className="flex items-center gap-3">
                  <div className="flex size-10 items-center justify-center rounded-xl bg-gradient-to-br from-primary/10 to-primary/5 text-primary ring-1 ring-primary/10">
                    <MessageSquareText className="size-5" />
                  </div>
                  <div>
                    <CardTitle className="text-lg">LLM 用户提示词模板</CardTitle>
                    <CardDescription>
                      自定义 user 角色 prompt 模板，留空使用内置默认
                    </CardDescription>
                  </div>
                </div>
              </CardHeader>
              <CardContent className="pt-5">
                <FormField
                  control={form.control}
                  name="llm_user_prompt"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>用户提示词模板</FormLabel>
                      <FormControl>
                        <Textarea
                          rows={10}
                          placeholder="留空使用内置默认用户提示词模板"
                          className="font-mono text-sm"
                          {...field}
                        />
                      </FormControl>
                      <FormDescription>
                        可用占位符：
                        <code className="mx-1 rounded bg-muted px-1 py-0.5 text-xs">{'{rubric}'}</code>
                        <code className="mx-1 rounded bg-muted px-1 py-0.5 text-xs">{'{output_format}'}</code>
                        <code className="mx-1 rounded bg-muted px-1 py-0.5 text-xs">{'{ocr_text}'}</code>
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </CardContent>
            </Card>

            <Separator className="my-1" />

            <div className="-mx-2 flex justify-end rounded-xl border border-border bg-muted/50 p-4">
              <Button
                type="submit"
                size="lg"
                disabled={updateMutation.isPending}
                className="gap-2 px-8 text-base"
              >
                {updateMutation.isPending ? (
                  <Loader2 className="animate-spin" />
                ) : (
                  <Save className="size-4" />
                )}
                保存配置
              </Button>
            </div>
          </form>
        </Form>
      )}

      <ProfileDialog
        dialog={dialog}
        busy={createMutation.isPending || renameMutation.isPending}
        onSubmit={handleDialogSubmit}
        onClose={() => setDialog({ open: false, mode: 'create' })}
      />

      <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除配置项目</AlertDialogTitle>
            <AlertDialogDescription>
              确定删除配置项目「{selectedProfile?.name}」？该项目的配置将从当前生效集合中移除。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction onClick={handleDelete} disabled={deleteMutation.isPending}>
              {deleteMutation.isPending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : null}
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function ProfileDialog({
  dialog,
  busy,
  onSubmit,
  onClose,
}: {
  dialog: ProfileDialogState;
  busy: boolean;
  onSubmit: (name: string) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState(dialog.profileName ?? '');

  // 打开时同步初始化名称
  useEffect(() => {
    if (dialog.open) {
      setName(dialog.profileName ?? '');
    }
  }, [dialog.open, dialog.profileName]);

  const title =
    dialog.mode === 'create'
      ? '新建配置项目'
      : dialog.mode === 'rename'
        ? '重命名配置项目'
        : '复制配置项目';
  const description =
    dialog.mode === 'create'
      ? '创建一套全新的独立配置'
      : dialog.mode === 'rename'
        ? '修改当前配置项目名称'
        : '基于当前项目复制一套全新配置，可在此基础上微调';

  const busyNow = dialog.mode === 'copy' ? false : busy;

  return (
    <AlertDialog open={dialog.open} onOpenChange={(open) => !open && onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            onSubmit(name);
          }}
          className="flex flex-col gap-3"
        >
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="如：初二语文 / 期中考冲刺"
            autoFocus
          />
        </form>
        <AlertDialogFooter>
          <AlertDialogCancel type="button">取消</AlertDialogCancel>
          <AlertDialogAction
            type="submit"
            disabled={busyNow}
          >
            {busyNow ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              '确定'
            )}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
