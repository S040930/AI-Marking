import { FileText } from 'lucide-react';

interface EmptyProps {
  text: string;
}

export function Empty({ text }: EmptyProps) {
  return (
    <div className="animate-fade-in-up motion-reduce:animate-none flex flex-col items-center gap-2 py-10 text-muted-foreground">
      <div className="flex size-10 items-center justify-center rounded-full bg-muted">
        <FileText className="size-5" />
      </div>
      <span className="text-sm">{text}</span>
    </div>
  );
}
