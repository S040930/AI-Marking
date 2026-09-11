import { memo, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import type { ReactNode } from 'react';
import { useLanguage } from '@/i18n';
import { CopyButton } from './CopyButton';

/**
 * 助手消息的受限 Markdown 渲染:Codex 输出中的 ``` 代码块、`行内代码`、
 * 列表与标题按真实排版渲染,而不是裸露符号。不渲染图片与 HTML,
 * 链接在新标签打开。代码块右上角带悬浮复制按钮。
 */

/** 包一层相对定位容器 + 悬浮复制按钮;复制文本从渲染结果读取。 */
function PreWithCopy({ children }: { children?: ReactNode }) {
  const { t } = useLanguage();
  const preRef = useRef<HTMLPreElement>(null);
  return (
    <div className="group/code relative my-2 max-w-full">
      <pre
        ref={preRef}
        className="overflow-x-auto rounded-xl border border-border/60 bg-muted/50 px-3 py-2.5 text-xs leading-relaxed text-foreground/90"
      >
        {children}
      </pre>
      <div className="absolute right-1.5 top-1.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover/code:opacity-100">
        <CopyButton
          text={() => preRef.current?.textContent ?? ''}
          label={t('复制代码')}
          className="border border-border/60 bg-background/80 backdrop-blur-sm"
        />
      </div>
    </div>
  );
}

const components = {
  pre: PreWithCopy,
  code: ({
    className,
    children,
    ...rest
  }: {
    className?: string;
    children?: ReactNode;
  }) => {
    // 块级代码由 pre 决定底色;行内代码补胶囊样式。
    const inline = !className?.includes('language-');
    if (!inline) {
      return (
        <code className={className} {...rest}>
          {children}
        </code>
      );
    }
    return (
      <code
        className="rounded border border-border/60 bg-muted/70 px-1 py-0.5 text-[0.85em] break-words"
        {...rest}
      >
        {children}
      </code>
    );
  },
  a: ({ href, children }: { href?: string; children?: ReactNode }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-primary underline underline-offset-2 hover:opacity-80"
    >
      {children}
    </a>
  ),
  img: () => null,
  table: ({ children }: { children?: ReactNode }) => (
    <div className="my-2 max-w-full overflow-x-auto">
      <table className="w-full border-collapse text-xs">{children}</table>
    </div>
  ),
  th: ({ children }: { children?: ReactNode }) => (
    <th className="border border-border bg-muted/60 px-2 py-1 text-left font-medium">
      {children}
    </th>
  ),
  td: ({ children }: { children?: ReactNode }) => (
    <td className="border border-border px-2 py-1 align-top">{children}</td>
  ),
};

/** 顶层间距收紧:转录里相邻块靠 gap 分隔,markdown 自身不需要大边距。 */
const blockClass =
  'text-sm leading-relaxed text-foreground/90 [&>*:first-child]:mt-0 [&>*:last-child]:mb-0 [&_h1,h2,h3,h4]:mt-3 [&_h1,h2,h3,h4]:mb-1 [&_h1,h2,h3,h4]:text-sm [&_h1,h2,h3,h4]:font-semibold [&_p]:my-1.5 [&_ul]:my-1.5 [&_ol]:my-1.5 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:list-decimal [&_ol]:pl-5 [&_li]:my-0.5 [&_blockquote]:my-2 [&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground [&_hr]:my-3 [&_hr]:border-border/60';

export const MarkdownContent = memo(function MarkdownContent({
  text,
}: {
  text: string;
}) {
  return (
    <div className={blockClass}>
      <ReactMarkdown
        components={components}
        disallowedElements={['img', 'script', 'style', 'iframe']}
        skipHtml
      >
        {text}
      </ReactMarkdown>
    </div>
  );
});
