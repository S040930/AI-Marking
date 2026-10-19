import { Link } from 'react-router-dom';
import { Check, FileUp, History, Upload } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { useLanguage } from '@/i18n';
import { PageHeader } from '@/components/common/PageHeader';

export default function HomePage() {
  const { t } = useLanguage();

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader
        className="mb-6"
        title={t('AI 作业批改')}
        description={t(
          '网页端管理题目、上传学生作业，由 AI 批改 agent 评分并确认最终成绩。',
        )}
      />

      <Card className="elevated-card overflow-hidden">
        <CardHeader className="pb-4">
          <div className="flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-xl bg-primary/10 text-primary">
              <Check className="size-5" />
            </div>
            <div>
              <CardTitle className="text-lg">{t('批改流程')}</CardTitle>
            </div>
          </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-6 pt-2">
          <ol className="flex flex-col gap-4">
            <li className="flex items-start gap-3">
              <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-sm font-semibold text-primary">
                1
              </span>
              <div>
                <p className="font-medium text-foreground">{t('准备题目')}</p>
                <p className="mt-0.5 text-sm text-muted-foreground">
                  {t(
                    '先在题目库上传并识别题目 PDF，题目只需上传一次，之后可反复使用。',
                  )}
                </p>
              </div>
            </li>
            <li className="flex items-start gap-3">
              <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-sm font-semibold text-primary">
                2
              </span>
              <div>
                <p className="font-medium text-foreground">
                  {t('网页上传作业')}
                </p>
                <p className="mt-0.5 text-sm text-muted-foreground">
                  {t(
                    '上传学生报告 PDF 与代码文件（或包含全部文件的 ZIP），在详情页选择模型通道后开始批改，AI 批改 agent 自动完成评分。',
                  )}
                </p>
              </div>
            </li>
            <li className="flex items-start gap-3">
              <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-sm font-semibold text-primary">
                3
              </span>
              <div>
                <p className="font-medium text-foreground">
                  {t('在网页确认成绩')}
                </p>
                <p className="mt-0.5 text-sm text-muted-foreground">
                  {t(
                    '评分建议生成后进入批改详情页核对，必要时人工调整，确认后提交最终成绩。',
                  )}
                </p>
              </div>
            </li>
          </ol>

          <div className="flex flex-col gap-3 border-t pt-5 sm:flex-row">
            <Button asChild className="btn-press w-full sm:w-auto">
              <Link to="/submissions/upload">
                <FileUp />
                {t('上传作业')}
              </Link>
            </Button>
            <Button asChild className="btn-press w-full sm:w-auto">
              <Link to="/questions/upload">
                <Upload />
                {t('上传题目')}
              </Link>
            </Button>
            <Button asChild variant="outline" className="w-full sm:w-auto">
              <Link to="/history">
                <History />
                {t('查看历史记录')}
              </Link>
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
