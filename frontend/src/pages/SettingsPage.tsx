import { useEffect } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { toast } from 'sonner';
import { Loader2, AlertCircle } from 'lucide-react';

import {
  type ConfigOut,
  useConfig,
  useUpdateConfig,
} from '@/api/config';
import { Card, CardContent } from '@/components/ui/card';
import {
  Alert,
  AlertDescription,
  AlertTitle,
} from '@/components/ui/alert';
import { ConfigSettingsForm } from '@/components/settings/ConfigSettingsForm';
import {
  AcpAgentDirectory,
  AcpAgentDirectoryHint,
} from '@/components/settings/AcpAgentDirectory';
import { useLanguage } from '@/i18n';
import { PageHeader } from '@/components/common/PageHeader';
import {
  configSchema,
  defaultConfigFormValues,
  errorText,
  type ConfigFormValues,
} from '@/lib/settingsForm';

export default function SettingsPage() {
  const { t } = useLanguage();
  const { data, isLoading, isError, error } = useConfig();
  const updateMutation = useUpdateConfig();

  const form = useForm<ConfigFormValues>({
    resolver: zodResolver(configSchema),
    defaultValues: defaultConfigFormValues,
  });

  useEffect(() => {
    if (data) {
      form.reset({
        paddleocr_api_url: data.paddleocr_api_url,
        paddleocr_token: data.paddleocr_token,
        rubric_definition: data.rubric_definition
          ? JSON.stringify(data.rubric_definition, null, 2)
          : '',
        review_enabled: data.review_enabled,
      });
    }
  }, [data, form]);

  const onSubmit = (values: ConfigFormValues) => {
    let rubric_definition: ConfigOut['rubric_definition'] = null;
    if (values.rubric_definition?.trim()) {
      try {
        rubric_definition = JSON.parse(values.rubric_definition) as ConfigOut['rubric_definition'];
      } catch {
        toast.error(t('Rubric 必须是合法 JSON'));
        return;
      }
    }
    const { rubric_definition: _raw, ...rest } = values;
    updateMutation.mutate({ ...rest, rubric_definition }, {
      onSuccess: () => {
        toast.success(t('配置已保存'));
      },
      onError: (err) => {
        toast.error(errorText(err));
      },
    });
  };

  const handleResetRubric = () => {
    form.setValue('rubric_definition', '', { shouldDirty: true });
    toast.info(t('Rubric 已清空，保存后评分标准将由客户端从题目中提取'));
  };

  if (isLoading) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-32">
        <Loader2 className="size-8 animate-spin text-primary" />
        <p className="text-sm text-muted-foreground">{t('加载配置中...')}</p>
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
              <AlertTitle>{t('加载配置失败')}</AlertTitle>
              <AlertDescription>{error?.message}</AlertDescription>
            </Alert>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader
        className="mb-6"
        title={t('系统设置')}
        description={t('配置 OCR 解析、评分标准与 MCP 自检开关')}
      />

      {/* 批改助手目录(ACP) */}
      <AcpAgentDirectory />
      <AcpAgentDirectoryHint />

      <ConfigSettingsForm
        form={form}
        isSaving={updateMutation.isPending}
        onSubmit={onSubmit}
        onResetRubric={handleResetRubric}
      />
    </div>
  );
}
