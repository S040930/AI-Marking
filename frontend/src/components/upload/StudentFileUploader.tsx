import { BookOpen } from 'lucide-react';
import { FileSlot } from '@/components/upload/FileSlot';

interface StudentFileUploaderProps {
  file: File | null;
  onFileChange: (file: File | null) => void;
  questionName?: string;
}

export function StudentFileUploader({
  file,
  onFileChange,
  questionName,
}: StudentFileUploaderProps) {
  return (
    <div className="space-y-5">
      {questionName && (
        <div className="flex items-center gap-3 rounded-xl border border-primary/20 bg-primary/5 px-4 py-3">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <BookOpen className="size-4" />
          </div>
          <div className="min-w-0">
            <p className="text-xs text-muted-foreground">已选题目</p>
            <p className="truncate text-sm font-semibold text-foreground">
              {questionName}
            </p>
          </div>
        </div>
      )}

      <FileSlot
        label="学生作业文件"
        description="学生提交的作业内容"
        file={file}
        onFile={onFileChange}
      />
    </div>
  );
}
