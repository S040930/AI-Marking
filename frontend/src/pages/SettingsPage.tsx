import { useEffect } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { toast } from 'sonner';
import {
  Loader2,
  Save,
  Undo2,
  AlertCircle,
  Bot,
  ScanEye,
  ClipboardCheck,
  MessageSquareText,
  User,
} from 'lucide-react';
import { useConfig, useUpdateConfig } from '@/api/config';
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
import {
  Alert,
  AlertDescription,
  AlertTitle,
} from '@/components/ui/alert';

const DEFAULT_RUBRIC_PLACEHOLDER = `留空则按以下优先级使用:
1. 题目 PDF 中识别出的评分标准
2. 内置默认 rubric(5 维度 100 分)

内置默认 rubric 仅供参考:
评分维度:
1. 内容理解(30分)
2. 论证分析(30分)
3. 结构组织(20分)
4. 语言表达(10分)
5. 规范性(10分)
总分:100分`;

const configSchema = z.object({
  llm_api_key: z.string().optional(),
  llm_base_url: z.string().optional(),
  llm_model: z.string().optional(),
  paddleocr_api_url: z.string().optional(),
  paddleocr_token: z.string().optional(),
  rubric: z.string().optional(),
  llm_user_prompt: z.string().optional(),
  operator_name: z.string().max(100).optional(),
});

type ConfigFormValues = z.infer<typeof configSchema>;

const defaultValues: ConfigFormValues = {
  llm_api_key: '',
  llm_base_url: '',
  llm_model: '',
  paddleocr_api_url: '',
  paddleocr_token: '',
  rubric: '',
  llm_user_prompt: '',
  operator_name: '',
};

export default function SettingsPage() {
  const { data, isLoading, isError, error } = useConfig();
  const updateMutation = useUpdateConfig();

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
        paddleocr_api_url: data.paddleocr_api_url,
        paddleocr_token: data.paddleocr_token,
        rubric: data.rubric,
        llm_user_prompt: data.llm_user_prompt,
        operator_name: data.operator_name,
      });
    }
  }, [data, form]);

  const onSubmit = (values: ConfigFormValues) => {
    updateMutation.mutate(values, {
      onSuccess: () => {
        toast.success('配置已保存');
      },
      onError: (err) => {
        toast.error(err.message || '保存失败');
      },
    });
  };

  const handleResetRubric = () => {
    form.setValue('rubric', '', { shouldDirty: true });
    toast.info('Rubric 已清空，保存后将使用默认 rubric');
  };

  if (isLoading) {
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
          配置大模型、OCR 解析与评分标准
        </p>
      </div>

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
                      系统会自动补全 /layout-parsing 后缀
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

          <Card className="elevated-card stagger-3 animate-fade-in-up motion-reduce:animate-none overflow-hidden">
            <CardHeader className="pb-4">
              <div className="flex items-start justify-between gap-4">
                <div className="flex items-center gap-3">
                  <div className="flex size-10 items-center justify-center rounded-xl bg-gradient-to-br from-primary/10 to-primary/5 text-primary ring-1 ring-primary/10">
                    <ClipboardCheck className="size-5" />
                  </div>
                  <div>
                    <CardTitle className="text-lg">评分标准(Rubric)</CardTitle>
                    <CardDescription>
                      自定义评分维度与权重；留空使用内置默认 rubric
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
                name="rubric"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>自定义 Rubric</FormLabel>
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

          <Card className="elevated-card stagger-5 animate-fade-in-up motion-reduce:animate-none overflow-hidden">
            <CardHeader className="pb-4">
              <div className="flex items-center gap-3">
                <div className="flex size-10 items-center justify-center rounded-xl bg-gradient-to-br from-primary/10 to-primary/5 text-primary ring-1 ring-primary/10">
                  <User className="size-5" />
                </div>
                <div>
                  <CardTitle className="text-lg">操作人信息</CardTitle>
                  <CardDescription>
                    提交最终评分时作为 reviewer_name 使用
                  </CardDescription>
                </div>
              </div>
            </CardHeader>
            <CardContent className="pt-5">
              <FormField
                control={form.control}
                name="operator_name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>审核教师姓名</FormLabel>
                    <FormControl>
                      <Input
                        placeholder="请输入审核教师姓名"
                        maxLength={100}
                        {...field}
                      />
                    </FormControl>
                    <FormDescription>
                      留空时使用默认值 Teacher
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
    </div>
  );
}
