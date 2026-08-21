import type { UseFormReturn } from 'react-hook-form';
import { ClipboardCheck, Loader2, Save, ScanEye, ShieldCheck, Undo2 } from 'lucide-react';
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
import { Checkbox } from '@/components/ui/checkbox';
import { Button } from '@/components/ui/button';
import { Separator } from '@/components/ui/separator';
import { useLanguage } from '@/i18n';
import {
  DEFAULT_RUBRIC_PLACEHOLDER,
  EN_RUBRIC_PLACEHOLDER,
  type ConfigFormValues,
} from '@/lib/settingsForm';

interface ConfigSettingsFormProps {
  form: UseFormReturn<ConfigFormValues>;
  isSaving: boolean;
  onSubmit: (values: ConfigFormValues) => void;
  onResetRubric: () => void;
}

export function ConfigSettingsForm({
  form,
  isSaving,
  onSubmit,
  onResetRubric,
}: ConfigSettingsFormProps) {
  const { locale, t } = useLanguage();

  return (
    <Form {...form}>
      <form
        onSubmit={form.handleSubmit(onSubmit)}
        className="flex flex-col gap-5"
      >
        <Card className="elevated-card stagger-1 animate-fade-in-up motion-reduce:animate-none overflow-hidden">
          <CardHeader className="pb-4">
            <div className="flex items-center gap-3">
              <div className="flex size-10 items-center justify-center rounded-xl bg-gradient-to-br from-primary/10 to-primary/5 text-primary ring-1 ring-primary/10">
                <ShieldCheck className="size-5" />
              </div>
              <div>
                  <CardTitle className="text-lg">{t('MCP 评分自检')}</CardTitle>
                <CardDescription>
                  {t('编程助手(Codex 等)通过本地 MCP 接口完成评分与复核')}
                </CardDescription>
              </div>
            </div>
          </CardHeader>
          <CardContent className="pt-5">
            <FormField
              control={form.control}
              name="review_enabled"
              render={({ field }) => (
                <FormItem className="flex items-start gap-3 space-y-0">
                  <FormControl>
                    <Checkbox
                      checked={field.value}
                      onCheckedChange={field.onChange}
                      className="mt-0.5"
                    />
                  </FormControl>
                  <div className="space-y-1 leading-none">
                    <FormLabel>{t('要求客户端在保存建议前完成第二遍反向自检')}</FormLabel>
                    <FormDescription>
                      {t('评分流程要求 MCP 客户端对每条评分项做反向校验（依据原文引用与分数上限推导），双重检查通过后才能保存建议，最终成绩仍需教师在此网页确认。')}
                    </FormDescription>
                  </div>
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
                  <CardTitle className="text-lg">{t('PaddleOCR-VL 文档解析')}</CardTitle>
                <CardDescription>
                  {t('将 PDF 解析为结构化 Markdown，保留表格、公式与阅读顺序')}
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
                    {t('请填写完整接口：异步任务入口 .../api/v2/ocr/jobs，或同步入口 .../layout-parsing；不要填写 .../api/v2/ocr/jobs/layout-parsing。')}
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
                      placeholder={t('请输入 PaddleOCR Access Token')}
                      autoComplete="new-password"
                      {...field}
                    />
                  </FormControl>
                  <FormDescription>
                    {t('AI Studio 个人访问令牌')}
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
                  <CardTitle className="text-lg">{t('评分标准(Rubric)')}</CardTitle>
                  <CardDescription>
                    {t('配置的评分标准优先于题目提取结果；留空时由客户端从题目中提取')}
                  </CardDescription>
                </div>
              </div>
              <CardAction>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={onResetRubric}
                >
                  <Undo2 className="size-4" />
                  {t('重置为默认')}
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
                  <FormLabel>{t('结构化 Rubric JSON')}</FormLabel>
                  <FormControl>
                    <Textarea
                      rows={10}
                      placeholder={locale === 'zh-CN' ? DEFAULT_RUBRIC_PLACEHOLDER : EN_RUBRIC_PLACEHOLDER}
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

        <Separator className="my-1" />

        <div className="-mx-2 flex justify-end rounded-xl border border-border bg-muted/50 p-4">
          <Button
            type="submit"
            size="lg"
            disabled={isSaving}
            className="gap-2 px-8 text-base"
          >
            {isSaving ? (
              <Loader2 className="animate-spin" />
            ) : (
              <Save className="size-4" />
            )}
            {t('保存配置')}
          </Button>
        </div>
      </form>
    </Form>
  );
}
