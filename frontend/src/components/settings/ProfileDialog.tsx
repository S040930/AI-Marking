import { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { Input } from '@/components/ui/input';
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
import { useLanguage } from '@/i18n';
import { type ProfileDialogState } from '@/lib/settingsForm';

export function ProfileDialog({
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
  const { t } = useLanguage();
  const [name, setName] = useState(dialog.profileName ?? '');

  // 打开时同步初始化名称
  useEffect(() => {
    if (dialog.open) {
      setName(dialog.profileName ?? '');
    }
  }, [dialog.open, dialog.profileName]);

  const title =
    dialog.mode === 'create'
      ? t('新建配置项目')
      : dialog.mode === 'rename'
        ? t('重命名配置项目')
        : t('复制配置项目');
  const description =
    dialog.mode === 'create'
      ? t('创建一套全新的独立配置')
      : dialog.mode === 'rename'
        ? t('修改当前配置项目名称')
        : t('基于当前项目复制一套全新配置，可在此基础上微调');

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
            placeholder={t('如：初二语文 / 期中考冲刺')}
            autoFocus
          />
        </form>
        <AlertDialogFooter>
          <AlertDialogCancel type="button">{t('取消')}</AlertDialogCancel>
          <AlertDialogAction
            type="submit"
            disabled={busyNow}
          >
            {busyNow ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              t('确定')
            )}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
