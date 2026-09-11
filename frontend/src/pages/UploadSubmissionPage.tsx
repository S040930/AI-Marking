import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Archive,
  Braces,
  Database,
  FileText,
  FileUp,
  Loader2,
  Upload,
  X,
} from 'lucide-react';
import { toast } from 'sonner';
import {
  useCreateSubmission,
  type SubmissionCodeInput,
} from '@/api/submissions';
import { useQuestions } from '@/api/questions';
import { useAcpAgents } from '@/api/acp';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import {
  DOCUMENT_INPUT_ACCEPT,
  MAX_DOCUMENT_SIZE_BYTES,
  MAX_ZIP_SIZE_BYTES,
} from '@/lib/documentUpload';
import {
  analyzeZip,
  sniffUploadKind,
  type ZipPreview,
} from '@/lib/zipPreview';
import { errorMessage } from '@/lib/questionErrors';
import { useLanguage } from '@/i18n';
import { PageHeader } from '@/components/common/PageHeader';

// 镜像后端 code_manifest.auto_question_number：文件名 q<N>.<ext> 自动推导小题号
function autoQuestionNumber(filename: string): number | null {
  const match = /q(\d+)\.[A-Za-z0-9_.-]+$/i.exec(filename);
  return match ? Number(match[1]) : null;
}

function formatZipSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

interface CodeEntry {  id: string;
  file: File;
  questionNumber: string;
}

type GradingMode = 'acp' | 'mcp';

export default function UploadSubmissionPage() {
  const { t } = useLanguage();
  const navigate = useNavigate();
  const createMutation = useCreateSubmission();
  const { data: questionsData } = useQuestions('', 50);
  // memo:否则每次渲染都造新数组,下方预选 effect 会被判定为依赖变化而每渲染重跑
  const questions = useMemo(() => questionsData?.items ?? [], [questionsData]);

  const [questionId, setQuestionId] = useState('');
  const [pdf, setPdf] = useState<File | null>(null);
  const [codeEntries, setCodeEntries] = useState<CodeEntry[]>([]);
  // 每道小题(小题号)各自选择一个入口文件,镜像后端“每题恰有一个 entrypoint”约束
  const [entrypointIds, setEntrypointIds] = useState<Record<number, string>>({});
  const [mode, setMode] = useState<GradingMode>('acp');
  // ZIP 上传:pdf 状态复用为“作业文件”;zipMode 时它是 .zip,zipPreview 为其只读预览
  const [zipPreview, setZipPreview] = useState<ZipPreview | null>(null);
  const [zipParsing, setZipParsing] = useState(false);
  const pdfInputRef = useRef<HTMLInputElement>(null);
  const codeInputRef = useRef<HTMLInputElement>(null);
  // ZIP 解析竞态守卫:每次 addAssignmentFile 自增,旧解析结果据此作废
  const zipTokenRef = useRef(0);
  const { data: agentsData, isLoading: agentsLoading } = useAcpAgents();
  // 本机没有可用的 Codex 连接时提前提示,避免上传后才发现问题。
  const codexReady = (agentsData?.agents ?? []).some(
    (agent) => agent.agent_id === 'codex-acp' && agent.connection_status === 'ready',
  );

  const zipMode = pdf !== null && pdf.name.toLowerCase().endsWith('.zip');
  const zipHasErrors = zipPreview !== null && zipPreview.errors.length > 0;

  const questionsEmpty = questions.length === 0;
  const canSubmit =
    questionId !== '' && pdf !== null && (!zipMode || (zipPreview !== null && !zipHasErrors));

  // 题目上传页完成 OCR 后跳转过来时，按名称预选刚识别好的题目
  useEffect(() => {
    if (questionId !== '' || questionsEmpty) return;
    const raw = sessionStorage.getItem('ocr-ready-questions');
    if (!raw) return;
    sessionStorage.removeItem('ocr-ready-questions');
    try {
      const { names } = JSON.parse(raw) as { names?: string[] };
      const match = questions.find((q) => names?.includes(q.name));
      if (match) {
        setQuestionId(match.id);
        toast.success(
          `${t('「')}${match.name}${t('」识别完成，已为你预选，可直接上传作业答案')}`,
        );
      }
    } catch {
      // 忽略损坏的标记
    }
  }, [questionId, questions, questionsEmpty, t]);

  const addAssignmentFile = async (file: File) => {
    const kind = sniffUploadKind(file.name);
    if (kind === null) {
      toast.error(t('文件仅支持 PDF 或 ZIP 格式'));
      return;
    }
    if (kind === 'pdf' && file.size > MAX_DOCUMENT_SIZE_BYTES) {
      toast.error(t('文件超过 50MB，请压缩后重试'));
      return;
    }
    if (kind === 'zip' && file.size > MAX_ZIP_SIZE_BYTES) {
      toast.error(t('ZIP 超过 150MB，请压缩后重试'));
      return;
    }
    // 竞态守卫:连续选择文件时,较慢的旧 analyzeZip 结果不得覆盖新文件
    // 的预览/状态。自增发生在校验通过后,校验失败的文件不影响在途解析。
    const token = ++zipTokenRef.current;
    setPdf(file);
    setCodeEntries([]);
    setEntrypointIds({});
    if (kind === 'zip') {
      setZipParsing(true);
      setZipPreview(null);
      try {
        const preview = await analyzeZip(file);
        if (token !== zipTokenRef.current) return;
        setZipPreview(preview);
      } catch (error) {
        if (token !== zipTokenRef.current) return;
        toast.error((error as Error).message);
        setPdf(null);
      } finally {
        // 过期调用不得清除新调用设置的解析状态
        if (token === zipTokenRef.current) {
          setZipParsing(false);
        }
      }
    } else {
      setZipPreview(null);
    }
  };

  const addCodeFiles = (files: File[]) => {
    const next: CodeEntry[] = files.map((file) => ({
      id: `${file.name}-${file.size}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      file,
      questionNumber: autoQuestionNumber(file.name)?.toString() ?? '',
    }));
    setCodeEntries((prev) => [...prev, ...next]);
    // 新文件落到的每个小题组若还没有入口,则自动担任入口
    setEntrypointIds((prev) => {
      const merged = { ...prev };
      for (const entry of next) {
        const number = Number(entry.questionNumber);
        if (!Number.isInteger(number) || number < 1) continue;
        if (merged[number] === undefined) merged[number] = entry.id;
      }
      return merged;
    });
  };

  const removeCodeEntry = (id: string) => {
    const removed = codeEntries.find((entry) => entry.id === id);
    setCodeEntries((prev) => prev.filter((entry) => entry.id !== id));
    if (!removed) return;
    const number = Number(removed.questionNumber);
    if (Number.isInteger(number) && number >= 1 && entrypointIds[number] === id) {
      // 移除的是所在小题的入口:入口顺延给组内下一个文件
      setEntrypointIds((ids) => {
        const nextIds = { ...ids };
        const successor = codeEntries.find(
          (entry) => entry.id !== id && Number(entry.questionNumber) === number,
        );
        if (successor) nextIds[number] = successor.id;
        else delete nextIds[number];
        return nextIds;
      });
    }
  };

  const clearCodeEntries = () => {
    setCodeEntries([]);
    setEntrypointIds({});
  };

  const validateCodeInputs = (): SubmissionCodeInput[] | null => {
    if (codeEntries.length === 0) return [];
    for (const entry of codeEntries) {
      const questionNumber = Number(entry.questionNumber);
      if (!Number.isInteger(questionNumber) || questionNumber < 1) {
        toast.error(
          `${t('「')}${entry.file.name}${t('」的小题号必须是正整数')}`,
        );
        return null;
      }
    }
    const inputs: SubmissionCodeInput[] = [];
    for (const entry of codeEntries) {
      const questionNumber = Number(entry.questionNumber);
      // 小题号还没填的文件先不要求入口;上面已拦截非法题号,这里必然有效
      if (entrypointIds[questionNumber] === undefined) {
        toast.error(t('每道小题需各选一个入口文件'));
        return null;
      }
      inputs.push({
        file: entry.file,
        questionNumber,
        entrypoint: entrypointIds[questionNumber] === entry.id,
      });
    }
    return inputs;
  };

  const handleSubmit = () => {
    if (questionId === '') {
      toast.error(t('请先选择题目'));
      return;
    }
    if (!pdf) {
      toast.error(t('请先选择报告 PDF'));
      return;
    }
    if (zipMode) {
      // ZIP:后端解包自动分类,不带散装代码清单
      createMutation.mutate(
        { file: pdf, questionId },
        {
          onSuccess: (res) => {
            if (mode === 'acp') {
              sessionStorage.setItem(`acp-assistant-prefill:${res.id}`, '1');
            }
            toast.success(t('作业已上传，正在进入批改流程'));
            navigate(`/review/${res.id}`);
          },
          onError: (error) => toast.error(errorMessage(error)),
        },
      );
      return;
    }
    const codeInputs = validateCodeInputs();
    if (codeInputs === null) return;

    createMutation.mutate(
      { file: pdf, questionId, codeInputs: codeInputs.length > 0 ? codeInputs : undefined },
      {
        onSuccess: (res) => {
          if (mode === 'acp') {
            // 意图标记：进入详情页后自动打开 AI 助手并填入批改指令，
            // 由教师确认模型、思考强度与权限档位后手动发送。
            sessionStorage.setItem(`acp-assistant-prefill:${res.id}`, '1');
          }
          toast.success(t('作业已上传，正在进入批改流程'));
          navigate(`/review/${res.id}`);
        },
        onError: (error) => toast.error(errorMessage(error)),
      },
    );
  };

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <PageHeader
        title={t('上传作业')}
        description={t(
          '在网页端上传学生作业：选择题目、报告 PDF 与代码文件，提交后选择评分方式，系统会自动进入批改流程。',
        )}
      />

      {/* 作业信息：题目 + 报告 PDF */}
      <Card className="elevated-card">
        <CardHeader className="pb-2">
          <CardTitle className="text-base">{t('作业信息')}</CardTitle>
          <CardDescription>
            {t('选择作业对应的题目，并上传学生报告 PDF。')}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5 pt-4">
          <div>
            <label className="text-sm font-medium text-foreground">
              {t('选择题目')}
            </label>
            {questionsEmpty ? (
              <div className="mt-2 flex items-center gap-2 text-sm text-muted-foreground">
                <span>{t('暂无题目，请先上传题目')}</span>
                <Button
                  variant="link"
                  size="sm"
                  className="px-0"
                  onClick={() => navigate('/questions/upload')}
                >
                  {t('前往上传题目')}
                </Button>
              </div>
            ) : (
              <select
                aria-label={t('选择题目')}
                value={questionId}
                onChange={(event) => setQuestionId(event.target.value)}
                className="mt-2 h-9 w-full rounded-md border border-input bg-background px-2.5 text-sm text-foreground shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              >
                <option value="">{t('请选择题目')}</option>
                {questions.map((question) => (
                  <option key={question.id} value={question.id}>
                    {question.name}
                  </option>
                ))}
              </select>
            )}
          </div>

          <div>
            <label className="text-sm font-medium text-foreground">
              {t('学生作业文件')}
            </label>
            <div
              role="button"
              tabIndex={0}
              aria-label={t('点击选择作业文件')}
              onClick={() => pdfInputRef.current?.click()}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  pdfInputRef.current?.click();
                }
              }}
              className="mt-2 flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-muted-foreground/30 px-6 py-8 text-center transition-colors hover:border-primary/50 hover:bg-muted/40"
            >
              <input
                ref={pdfInputRef}
                type="file"
                accept={DOCUMENT_INPUT_ACCEPT}
                aria-label={t('选择作业文件')}
                className="hidden"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void addAssignmentFile(file);
                  event.target.value = '';
                }}
              />
              <div className="flex size-12 items-center justify-center rounded-2xl bg-primary/10 text-primary">
                <FileUp className="size-6" />
              </div>
              {pdf ? (
                <p className="flex items-center gap-2 text-sm font-medium text-foreground">
                  {zipMode ? (
                    <Archive className="size-4 text-primary" />
                  ) : (
                    <FileText className="size-4 text-primary" />
                  )}
                  {pdf.name}
                  <span className="text-xs text-muted-foreground">
                    {(pdf.size / 1024 / 1024).toFixed(1)} MB
                  </span>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    aria-label={t('移除')}
                    onClick={(event) => {
                      event.stopPropagation();
                      setPdf(null);
                      setZipPreview(null);
                    }}
                  >
                    <X />
                  </Button>
                </p>
              ) : (
                <>
                  <p className="font-medium text-foreground">
                    {t('选择报告 PDF 或作业 ZIP')}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {t(
                      '支持纯 PDF(≤50MB),或 ZIP 包(≤150MB,内含报告 PDF、代码文件与数据集)',
                    )}
                  </p>
                </>
              )}
            </div>
          </div>
        </CardContent>
      </Card>

      {/* ZIP 预览（只读) */}
      {zipMode && (
        <Card className="elevated-card">
          <CardHeader className="pb-2">
            <CardTitle className="text-base">{t('ZIP 内容预览')}</CardTitle>
            <CardDescription>
              {t(
                '上传时 ZIP 由系统解包自动分类：报告 PDF、代码文件(文件名需以小题号结尾,如 task3.py)与其余文件将作为数据集随批改提供。',
              )}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4 pt-4">
            {zipParsing ? (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="size-4 animate-spin" />
                {t('正在解析 ZIP 内容...')}
              </div>
            ) : zipPreview ? (
              <div className="space-y-4">
                {zipPreview.errors.length > 0 && (
                  <ul className="space-y-1 rounded-xl bg-destructive/10 p-3 text-sm text-destructive">
                    {zipPreview.errors.map((error) => (
                      <li key={error}>{error}</li>
                    ))}
                  </ul>
                )}
                <div className="grid gap-3 sm:grid-cols-3">
                  <div className="rounded-xl border p-3">
                    <p className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                      <FileText className="size-3.5" />
                      {t('报告 PDF')}
                    </p>
                    {zipPreview.report ? (
                      <p className="mt-1.5 truncate text-sm text-foreground" title={zipPreview.report.name}>
                        {zipPreview.report.name}
                        <span className="ml-1 text-xs text-muted-foreground">
                          {formatZipSize(zipPreview.report.size)}
                        </span>
                      </p>
                    ) : (
                      <p className="mt-1.5 text-sm text-muted-foreground">
                        {t('未找到')}
                      </p>
                    )}
                  </div>
                  <div className="rounded-xl border p-3">
                    <p className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                      <Braces className="size-3.5" />
                      {t('代码文件')}
                      <span className="text-xs">{zipPreview.code.length}</span>
                    </p>
                    {zipPreview.code.length > 0 ? (
                      <ul className="mt-1.5 space-y-1">
                        {zipPreview.code.map((entry) => (
                          <li key={entry.name} className="truncate text-sm text-foreground" title={entry.name}>
                            {`Q${entry.questionNumber}`} · {entry.name}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="mt-1.5 text-sm text-muted-foreground">
                        {t('无代码文件')}
                      </p>
                    )}
                  </div>
                  <div className="rounded-xl border p-3">
                    <p className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                      <Database className="size-3.5" />
                      {t('数据集')}
                      <span className="text-xs">{zipPreview.datasets.length}</span>
                    </p>
                    {zipPreview.datasets.length > 0 ? (
                      <ul className="mt-1.5 space-y-1">
                        {zipPreview.datasets.map((entry) => (
                          <li key={entry.name} className="truncate text-sm text-foreground" title={entry.name}>
                            {entry.name}
                            <span className="ml-1 text-xs text-muted-foreground">
                              {formatZipSize(entry.size)}
                            </span>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="mt-1.5 text-sm text-muted-foreground">
                        {t('无数据集')}
                      </p>
                    )}
                  </div>
                </div>
                {zipPreview.skipped.length > 0 && (
                  <p className="text-xs text-muted-foreground">
                    {t('已忽略')}
                    {' '}
                    {zipPreview.skipped.length}
                    {' '}
                    {t('个系统文件(如 .DS_Store)')}
                  </p>
                )}
              </div>
            ) : null}
          </CardContent>
        </Card>
      )}

      {/* 代码文件（可选,仅纯 PDF 上传;ZIP 由后端自动分类) */}
      {!zipMode && (
      <Card className="elevated-card">
        <CardHeader className="pb-2">
          <CardTitle className="text-base">{t('代码文件（可选）')}</CardTitle>
          <CardDescription>
            {t(
              '支持多份代码文件，为每份指定所属小题；同一小题有多个文件时需标记一个入口文件。',
            )}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4 pt-4">
          <div
            role="button"
            tabIndex={0}
            aria-label={t('选择代码文件')}
            onClick={() => codeInputRef.current?.click()}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                codeInputRef.current?.click();
              }
            }}
            className="flex cursor-pointer items-center justify-center gap-2 rounded-xl border-2 border-dashed border-muted-foreground/30 px-6 py-6 text-center transition-colors hover:border-primary/50 hover:bg-muted/40"
          >
            <input
              ref={codeInputRef}
              type="file"
              multiple
              aria-label={t('选择代码文件输入框')}
              className="hidden"
              onChange={(event) => {
                addCodeFiles(Array.from(event.target.files ?? []));
                event.target.value = '';
              }}
            />
            <FileUp className="size-5 text-primary" />
            <span className="text-sm font-medium text-foreground">
              {t('选择代码文件')}
            </span>
          </div>

          {codeEntries.length > 0 && (
            <ul className="divide-y rounded-xl border">
              {codeEntries.map((entry) => (
                <li
                  key={entry.id}
                  className="flex flex-wrap items-center gap-3 px-4 py-3"
                >
                  <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
                    <FileText className="size-4" />
                  </span>
                  <span className="min-w-0 flex-1 truncate text-sm text-foreground">
                    {entry.file.name}
                  </span>
                  <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                    {t('小题号')}
                    <Input
                      type="number"
                      min={1}
                      value={entry.questionNumber}
                      onChange={(event) => {
                        const oldNumber = Number(entry.questionNumber);
                        const newNumber = Number(event.target.value);
                        setCodeEntries((prev) =>
                          prev.map((item) =>
                            item.id === entry.id
                              ? { ...item, questionNumber: event.target.value }
                              : item,
                          ),
                        );
                        // 文件换组:原小题入口顺延给组内其他文件,新组没有入口时由它担任
                        if (
                          Number.isInteger(oldNumber) &&
                          oldNumber >= 1 &&
                          entrypointIds[oldNumber] === entry.id
                        ) {
                          const successor = codeEntries.find(
                            (item) =>
                              item.id !== entry.id &&
                              Number(item.questionNumber) === oldNumber,
                          );
                          setEntrypointIds((ids) => {
                            const nextIds = { ...ids };
                            if (successor) nextIds[oldNumber] = successor.id;
                            else delete nextIds[oldNumber];
                            return nextIds;
                          });
                        }
                        if (
                          Number.isInteger(newNumber) &&
                          newNumber >= 1 &&
                          entrypointIds[newNumber] === undefined
                        ) {
                          setEntrypointIds((ids) => ({ ...ids, [newNumber]: entry.id }));
                        }
                      }}
                      className="h-8 w-20"
                      aria-label={`${t('小题号')} ${entry.file.name}`}
                    />
                  </label>
                  <label className="flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground">
                    <input
                      type="radio"
                      name={`code-entrypoint-${Number(entry.questionNumber) || 'unset'}`}
                      className="accent-primary"
                      aria-label={`${t('入口文件')} ${entry.file.name}`}
                      checked={
                        entrypointIds[Number(entry.questionNumber)] === entry.id &&
                        Number.isInteger(Number(entry.questionNumber)) &&
                        Number(entry.questionNumber) >= 1
                      }
                      onChange={() =>
                        setEntrypointIds((ids) => ({
                          ...ids,
                          [Number(entry.questionNumber)]: entry.id,
                        }))
                      }
                    />
                    {t('入口文件')}
                  </label>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={`${t('移除')} ${entry.file.name}`}
                    onClick={() => removeCodeEntry(entry.id)}
                  >
                    <X />
                  </Button>
                </li>
              ))}
            </ul>
          )}

          {codeEntries.length > 0 && (
            <div className="flex justify-end">
              <Button
                variant="ghost"
                size="sm"
                onClick={clearCodeEntries}
              >
                <X />
                {t('清空')}
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
      )}

      {/* 评分方式 */}
      <Card className="elevated-card">
        <CardHeader className="pb-2">
          <CardTitle className="text-base">{t('评分方式')}</CardTitle>
          <CardDescription>
            {t('选择本次作业的评分方式，上传后自动进入对应批改流程。')}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4 pt-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <label
              className={`flex cursor-pointer flex-col gap-1.5 rounded-xl border p-4 text-sm transition-colors ${
                mode === 'acp'
                  ? 'border-primary bg-primary/5'
                  : 'border-slate-200 hover:bg-slate-50'
              }`}
            >
              <span className="flex items-center gap-2 font-medium text-foreground">
                <input
                  type="radio"
                  name="grading-mode"
                  className="accent-primary"
                  checked={mode === 'acp'}
                  onChange={() => setMode('acp')}
                />
                {t('使用 ACP 自动批改')}
              </span>
              <span className="pl-5 text-xs leading-relaxed text-muted-foreground">
                {t('由本机已安装的批改助手在隔离工作区自动评分，上传后自动启动。')}
              </span>
            </label>
            <label
              className={`flex cursor-pointer flex-col gap-1.5 rounded-xl border p-4 text-sm transition-colors ${
                mode === 'mcp'
                  ? 'border-primary bg-primary/5'
                  : 'border-slate-200 hover:bg-slate-50'
              }`}
            >
              <span className="flex items-center gap-2 font-medium text-foreground">
                <input
                  type="radio"
                  name="grading-mode"
                  className="accent-primary"
                  checked={mode === 'mcp'}
                  onChange={() => setMode('mcp')}
                />
                {t('使用 MCP 等待外部编程助手')}
              </span>
              <span className="pl-5 text-xs leading-relaxed text-muted-foreground">
                {t('上传后由外部编程助手调用本地 MCP 评分，评分建议进入待审阅列表。')}
              </span>
            </label>
          </div>

          {mode === 'acp' ? (
            agentsLoading ? (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="size-4 animate-spin" />
                {t('加载助手目录中...')}
              </div>
            ) : codexReady ? (
              <p className="text-xs text-muted-foreground">
                {t('默认以 Ask for approval 档位启动，批改会话中可随时调整模型与权限。')}
              </p>
            ) : (
              <p className="rounded-xl bg-amber-50 p-3 text-sm text-amber-700">
                {t('请先在系统设置安装并连接测试 Codex ACP。')}
              </p>
            )
          ) : (
            <p className="text-sm text-muted-foreground">
              {t(
                '上传成功后跳转到批改详情页，等待外部编程助手（如 Codex）调用本地 MCP 评分。',
              )}
            </p>
          )}
        </CardContent>
      </Card>

      <div className="flex items-center justify-end border-t pt-4">
        <Button
          disabled={!canSubmit || createMutation.isPending}
          onClick={handleSubmit}
        >
          {createMutation.isPending ? (
            <Loader2 className="animate-spin" />
          ) : (
            <Upload />
          )}
          {t('开始上传')}
        </Button>
      </div>
    </div>
  );
}
