import type { UseFormReturn } from 'react-hook-form';
import { ClipboardCheck, Loader2, Save, ScanEye, Undo2 } from 'lucide-react';
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
import { useLanguage } from '@/i18n';
import { IconBadge } from '@/components/common/IconBadge';
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
              <IconBadge>
                <ScanEye className="size-5" />
              </IconBadge>
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

        <Card className="elevated-card stagger-2 animate-fade-in-up motion-reduce:animate-none overflow-hidden">
          <CardHeader className="pb-4">
            <div className="flex items-start justify-between gap-4">
              <div className="flex items-center gap-3">
                <IconBadge>
                  <ClipboardCheck className="size-5" />
                </IconBadge>
                <div>
                  <CardTitle className="text-lg">{t('评分标准(Rubric)')}</CardTitle>
                  <CardDescription>
                    {t('题目提取的评分标准优先；没有可信提取结果时使用当前配置')}
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
