import { useMemo } from "react";
import type { ReactNode } from "react";
import { Streamdown } from "streamdown";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { buildCitationAwareMarkdown, type ChatCitation } from "@/lib/chat";

function childrenToText(children: ReactNode) {
  return Array.isArray(children)
    ? children
        .map((child) => (typeof child === "string" ? child : ""))
        .join("")
    : typeof children === "string"
      ? children
      : "";
}

type MarkdownComponentProps = {
  children?: ReactNode;
  href?: string;
  title?: string;
};

function buildMarkdownComponents() {
  return {
    h1: ({ children }: MarkdownComponentProps) => (
      <h1 className="mb-2 text-[1.05rem] font-semibold leading-6 text-[#1f1c18]">
        {children}
      </h1>
    ),
    h2: ({ children }: MarkdownComponentProps) => (
      <h2 className="mb-1.5 text-[1rem] font-semibold leading-6 text-[#1f1c18]">
        {children}
      </h2>
    ),
    h3: ({ children }: MarkdownComponentProps) => (
      <h3 className="mb-1.5 text-[15px] font-semibold leading-6 text-[#1f1c18]">
        {children}
      </h3>
    ),
    h4: ({ children }: MarkdownComponentProps) => (
      <h4 className="mb-1 text-[14px] font-semibold leading-5 text-[#1f1c18]">
        {children}
      </h4>
    ),
    h5: ({ children }: MarkdownComponentProps) => (
      <h5 className="mb-1 text-[13px] font-semibold leading-5 text-[#1f1c18]">
        {children}
      </h5>
    ),
    h6: ({ children }: MarkdownComponentProps) => (
      <h6 className="mb-1 text-[12px] font-semibold leading-5 text-[#1f1c18]">
        {children}
      </h6>
    ),
    hr: () => <hr className="my-2 border-[#e8dece]" />,
    p: ({ children }: MarkdownComponentProps) => (
      <p className="mb-2 last:mb-0 text-[15px] leading-7 text-[#1f1c18]">{children}</p>
    ),
    ul: ({ children }: MarkdownComponentProps) => (
      <ul className="mb-2 list-disc space-y-1 pl-4.5 last:mb-0">{children}</ul>
    ),
    ol: ({ children }: MarkdownComponentProps) => (
      <ol className="mb-2 list-decimal space-y-1 pl-4.5 last:mb-0">{children}</ol>
    ),
    li: ({ children }: MarkdownComponentProps) => (
      <li className="text-[15px] leading-7 text-[#1f1c18]">{children}</li>
    ),
    table: ({ children }: MarkdownComponentProps) => (
      <div className="mb-3 overflow-x-auto rounded-[18px] border border-[#e4dccf] bg-[#fcfaf6]">
        <table className="min-w-full border-collapse text-left text-[13px] leading-5">
          {children}
        </table>
      </div>
    ),
    thead: ({ children }: MarkdownComponentProps) => (
      <thead className="border-b border-[#e4dccf] bg-[#f3ecdf]">{children}</thead>
    ),
    tbody: ({ children }: MarkdownComponentProps) => (
      <tbody className="[&_tr:last-child]:border-b-0">{children}</tbody>
    ),
    tr: ({ children }: MarkdownComponentProps) => (
      <tr className="border-b border-[#eee5d8] align-top">{children}</tr>
    ),
    th: ({ children }: MarkdownComponentProps) => (
      <th className="px-2.5 py-1.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-[#7b6f60]">
        {children}
      </th>
    ),
    td: ({ children }: MarkdownComponentProps) => (
      <td className="px-2.5 py-1.5 text-[13px] leading-5 text-[#2b241d]">{children}</td>
    ),
    a: ({ href, title, children }: MarkdownComponentProps) => {
      const linkText = childrenToText(children);
      const isCitationLink = /^\d+$/.test(linkText);

      if (isCitationLink && href) {
        return (
          <Tooltip>
            <TooltipTrigger asChild>
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                className="mx-0.5 inline-flex translate-y-[-0.22em] items-center justify-center rounded-full border border-[#d8c7a8] bg-[#f5ecdd] px-1.5 py-0 text-[10px] font-medium leading-4 text-[#7e5f2d] no-underline transition-colors hover:border-[#b99053] hover:bg-[#efdfc5]"
                aria-label={title || `Citation ${linkText}`}
              >
                {linkText}
              </a>
            </TooltipTrigger>
            {title && (
              <TooltipContent side="top" sideOffset={8} className="max-w-xs">
                {title}
              </TooltipContent>
            )}
          </Tooltip>
        );
      }

      return (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="text-[#8e6c34] underline decoration-[#d8c7a8] underline-offset-4 transition-colors hover:text-[#6f5226]"
          title={title}
        >
          {children}
        </a>
      );
    },
  };
}

interface ChatAnswerContentProps {
  content: string;
  citations: ChatCitation[];
}

export default function ChatAnswerContent({
  content,
  citations,
}: ChatAnswerContentProps) {
  const markdown = useMemo(
    () => buildCitationAwareMarkdown(content, citations),
    [content, citations]
  );
  const components = useMemo(() => buildMarkdownComponents(), []);

  return (
    <div className="min-w-0 text-[15px] leading-7 [&_blockquote]:my-2 [&_blockquote]:border-l-2 [&_blockquote]:border-[#d8c7a8] [&_blockquote]:pl-3 [&_blockquote]:text-[#5e584f]">
      <Streamdown
        className="space-y-1 text-[15px] leading-7"
        components={components as Record<string, React.ComponentType<any>>}
        controls={false}
      >
        {markdown}
      </Streamdown>
    </div>
  );
}
