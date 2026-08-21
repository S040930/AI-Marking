import { Copy, FolderKanban, Pencil, Plus, Star, Trash2 } from 'lucide-react';
import { type ConfigProfile } from '@/api/config';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { useLanguage } from '@/i18n';

interface ProfileManagerCardProps {
  profiles: ConfigProfile[] | undefined;
  selectedProfile: ConfigProfile | null;
  displayProfileId: number | undefined;
  onSelect: (id: number) => void;
  onOpenDialog: (mode: 'create' | 'rename' | 'copy') => void;
  onSetDefault: () => void;
  onRequestDelete: () => void;
}

export function ProfileManagerCard({
  profiles,
  selectedProfile,
  displayProfileId,
  onSelect,
  onOpenDialog,
  onSetDefault,
  onRequestDelete,
}: ProfileManagerCardProps) {
  const { t } = useLanguage();

  return (
    <Card className="elevated-card mb-5">
      <CardHeader className="pb-3">
        <div className="flex items-center gap-3">
          <div className="flex size-10 items-center justify-center rounded-xl bg-gradient-to-br from-primary/10 to-primary/5 text-primary ring-1 ring-primary/10">
            <FolderKanban className="size-5" />
          </div>
          <div>
            <CardTitle className="text-lg">{t('配置项目')}</CardTitle>
            <CardDescription>
              {t('每套配置独立管理，题目在上传时选择使用哪一套')}
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
              onClick={() => onSelect(p.id)}
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
            onClick={() => onOpenDialog('create')}
            className="gap-1.5 text-muted-foreground"
          >
            <Plus className="size-4" />
            {t('新建配置项目')}
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
                {t('默认')}
              </Badge>
            )}
            <div className="ml-auto flex flex-wrap gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => onOpenDialog('rename')}
                className="gap-1.5"
              >
                <Pencil className="size-3.5" />
                {t('重命名')}
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => onOpenDialog('copy')}
                className="gap-1.5"
              >
                <Copy className="size-3.5" />
                {t('复制')}
              </Button>
              {!selectedProfile.is_default && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={onSetDefault}
                  className="gap-1.5"
                >
                  <Star className="size-3.5" />
                  {t('设为默认')}
                </Button>
              )}
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={onRequestDelete}
                disabled={selectedProfile.is_default}
                className="gap-1.5 text-destructive hover:bg-destructive/10 hover:text-destructive"
              >
                <Trash2 className="size-3.5" />
                {t('删除')}
              </Button>
            </div>
          </div>
        )}
        {profiles && profiles.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {t('暂无配置项目，点击「新建配置项目」创建第一套配置')}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
