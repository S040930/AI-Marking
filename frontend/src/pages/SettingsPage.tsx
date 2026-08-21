import { useEffect, useMemo, useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { toast } from 'sonner';
import { Loader2, AlertCircle } from 'lucide-react';

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
import { Card, CardContent } from '@/components/ui/card';
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
import { ProfileDialog } from '@/components/settings/ProfileDialog';
import { ProfileManagerCard } from '@/components/settings/ProfileManagerCard';
import { ConfigSettingsForm } from '@/components/settings/ConfigSettingsForm';
import { useLanguage } from '@/i18n';
import {
  CLOSED_PROFILE_DIALOG,
  configSchema,
  defaultConfigFormValues,
  errorText,
  type ConfigFormValues,
  type ProfileDialogState,
} from '@/lib/settingsForm';

export default function SettingsPage() {
  const { t } = useLanguage();
  const { data: profiles, isLoading: profilesLoading } = useConfigProfiles();
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [dialog, setDialog] = useState<ProfileDialogState>(CLOSED_PROFILE_DIALOG);
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
      toast.error(t('请输入配置项目名称'));
      return;
    }
    if (dialog.mode === 'create') {
      createMutation.mutate(
        { name: trimmed },
        {
          onSuccess: (created) => {
            toast.success(t('配置项目已创建'));
            setDialog(CLOSED_PROFILE_DIALOG);
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
            toast.success(t('配置项目已重命名'));
            setDialog(CLOSED_PROFILE_DIALOG);
          },
          onError: (err) => toast.error(errorText(err)),
        },
      );
    } else if (dialog.mode === 'copy') {
      createMutation.mutate(
        { name: trimmed, copy_from_id: dialog.profileId },
        {
          onSuccess: (created) => {
            toast.success(t('已复制为新配置项目'));
            setDialog(CLOSED_PROFILE_DIALOG);
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
        toast.success(t('配置项目已删除'));
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
      onSuccess: () => toast.success(t('已设为默认配置项目')),
      onError: (err) => toast.error(errorText(err)),
    });
  };

  if (isLoading || profilesLoading) {
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
      <div className="mb-6">
        <h1 className="text-2xl font-bold tracking-tight text-foreground">
          {t('系统设置')}
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {t('配置 OCR 解析、评分标准与 MCP 自检开关，支持多套独立配置项目')}
        </p>
      </div>

      {/* 配置项目管理 */}
      <ProfileManagerCard
        profiles={profiles}
        selectedProfile={selectedProfile}
        displayProfileId={displayProfileId}
        onSelect={setSelectedId}
        onOpenDialog={openDialog}
        onSetDefault={handleSetDefault}
        onRequestDelete={() => setConfirmDelete(true)}
      />

      {!selectedProfile ? (
        <Card className="elevated-card">
          <CardContent className="pt-6">
            <Alert>
              <AlertTitle>{t('请选择一个配置项目')}</AlertTitle>
              <AlertDescription>
                {t('选择或新建配置项目后可编辑其详细配置')}
              </AlertDescription>
            </Alert>
          </CardContent>
        </Card>
      ) : (
        <ConfigSettingsForm
          form={form}
          isSaving={updateMutation.isPending}
          onSubmit={onSubmit}
          onResetRubric={handleResetRubric}
        />
      )}

      <ProfileDialog
        dialog={dialog}
        busy={createMutation.isPending || renameMutation.isPending}
        onSubmit={handleDialogSubmit}
        onClose={() => setDialog(CLOSED_PROFILE_DIALOG)}
      />

      <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t('删除配置项目')}</AlertDialogTitle>
            <AlertDialogDescription>
              {t('确定删除配置项目')}「{selectedProfile?.name}」？{t('该项目的配置将从当前生效集合中移除。')}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t('取消')}</AlertDialogCancel>
            <AlertDialogAction onClick={handleDelete} disabled={deleteMutation.isPending}>
              {deleteMutation.isPending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : null}
              {t('删除')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
