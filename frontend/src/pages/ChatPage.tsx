import { useEffect, useRef, useState } from "react";
import { ArrowUpRight } from "lucide-react";
import { useLocation } from "wouter";
import type { AiAttachment, AiSession } from "@/lib/ai-demo";
import { Button } from "@/components/ui/button";
import ChatComposer from "@/components/ChatComposer";
import { useImageAttachments } from "@/hooks/useImageAttachments";
import { cn } from "@/lib/utils";
import { formatChinaDateTimeShort } from "@/lib/time";

interface ChatPageProps {
  sessionId?: string;
  sessions: AiSession[];
  onCreateSession: (question: string, attachments?: AiAttachment[]) => string | null;
  onSendMessage: (sessionId: string, question: string, attachments?: AiAttachment[]) => void;
}

export default function ChatPage({
  sessionId,
  sessions,
  onCreateSession,
  onSendMessage,
}: ChatPageProps) {
  const [, setLocation] = useLocation();
  const [draft, setDraft] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const {
    attachments,
    appendFiles,
    removeAttachment,
    buildOutgoingAttachments,
    resetAttachments,
  } = useImageAttachments();
  const session = sessionId
    ? sessions.find((item) => item.id === sessionId) ?? null
    : null;

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [session?.messages.length]);

  const resetComposer = () => {
    setDraft("");
    resetAttachments();
  };

  const handleSubmit = async () => {
    const question = draft.trim();
    if ((!question && attachments.length === 0) || isSubmitting) {
      return;
    }

    try {
      setIsSubmitting(true);
      const attachments = await buildOutgoingAttachments();

      if (!sessionId) {
        const nextSessionId = onCreateSession(question, attachments);
        if (nextSessionId) {
          setLocation(`/chat/${nextSessionId}`);
        }
        resetComposer();
        return;
      }

      onSendMessage(sessionId, question, attachments);
      resetComposer();
    } finally {
      setIsSubmitting(false);
    }
  };

  if (sessionId && !session) {
    return (
      <div className="flex h-full min-h-0 items-center justify-center bg-[#f7f3eb] px-6 text-center">
        <div className="max-w-md space-y-4">
          <p className="font-display text-4xl text-[#2b241d]">Session not found</p>
          <p className="text-base leading-7 text-[#675f56]">
            当前历史会话不存在，可能已被清空。你可以直接开启一个新会话。
          </p>
          <Button onClick={() => setLocation("/chat/new")}>Start new chat</Button>
        </div>
      </div>
    );
  }

  return (
    <div className="relative flex h-full min-h-0 flex-col overflow-hidden overscroll-none bg-[#f7f3eb] text-[#1f1c18]">
      <header className="shrink-0 border-b border-[#e4dccf] bg-[#f7f3eb]/90 backdrop-blur">
        <div className="flex h-16 items-center justify-between px-5 md:px-8">
          <div>
            <p className="text-sm font-medium text-[#7d766d]">
              {session ? session.title : "New chat"}
            </p>
          </div>
          <div className="rounded-full border border-[#ddd4c7] bg-white px-3 py-1 text-xs text-[#7d766d]">
            {session?.scope?.type === "story"
              ? `Story context · ${session.scope.topicTitle}`
              : "Fashion Feed Knowledge"}
          </div>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto overscroll-contain px-4 py-6 pb-44 md:px-8 md:pb-52">
        {!session && (
          <div className="flex min-h-full items-center justify-center py-12">
            <div className="w-full max-w-3xl text-center">
              <h1 className="font-display text-5xl leading-tight text-[#2b241d]">
                What&apos;s on your mind today?
              </h1>
              <p className="mx-auto mt-4 max-w-xl text-base leading-7 text-[#6a6258]">
                Ask about trends, brands, market signals, or use the existing story feed
                as company knowledge.
              </p>
            </div>
          </div>
        )}

        {session && (
          <div className="mx-auto w-full max-w-4xl space-y-10 py-4">
            {session.messages.map((message) => (
              <div
                key={message.id}
                className={message.role === "user" ? "flex justify-end" : "flex justify-start"}
              >
                <div
                  className={cn(
                    "w-full max-w-3xl",
                    message.role === "user"
                      ? "max-w-2xl rounded-[24px] bg-[#f1e8da] px-5 py-4 text-[#1f1c18]"
                      : "text-[#1f1c18]"
                  )}
                >
                  <div className="mb-3 text-[11px] uppercase tracking-[0.18em] text-[#8a8379]">
                    {message.role === "user" ? "You" : "Fashion Feed AI"}
                  </div>
                  <div className="whitespace-pre-line text-[15px] leading-8">
                    {message.attachments && message.attachments.length > 0 && (
                      <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
                        {message.attachments.map((attachment) => (
                          <div
                            key={attachment.id}
                            className="overflow-hidden rounded-2xl border border-black/10 bg-black/5"
                          >
                            <img
                              src={attachment.dataUrl}
                              alt={attachment.name}
                              className="aspect-square w-full object-cover"
                              loading="lazy"
                            />
                          </div>
                        ))}
                      </div>
                    )}
                    {message.content && (
                      <div className="whitespace-pre-line text-[15px] leading-8">
                        {message.content}
                      </div>
                    )}
                  </div>
                  <div className="mt-3 text-xs text-[#8a8379]">
                    {formatChinaDateTimeShort(message.createdAt)}
                  </div>
                  {message.role === "assistant" &&
                    message.citations &&
                    message.citations.length > 0 && (
                      <div className="mt-4 grid gap-2">
                        {message.citations.map((citation) => (
                          <a
                            key={citation.id}
                            href={citation.href}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="rounded-2xl border border-[#e4dccf] bg-[#faf7f1] px-4 py-3 transition-colors hover:border-[#c8b18a]"
                          >
                            <div className="flex items-start justify-between gap-3">
                              <div className="min-w-0">
                                <p className="text-xs font-medium text-[#1f1c18]">
                                  {citation.topicTitle}
                                </p>
                                <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-[#6d655b]">
                                  {citation.sourceTitle}
                                </p>
                                <p className="mt-2 text-[11px] text-[#8b8479]">
                                  {citation.sourceName} · {citation.note}
                                </p>
                              </div>
                              <ArrowUpRight className="mt-0.5 h-4 w-4 shrink-0 text-[#9f7d45]" />
                            </div>
                          </a>
                        ))}
                      </div>
                    )}
                </div>
              </div>
            ))}
            <div ref={messagesEndRef} />
          </div>
        )}
      </div>

      <ChatComposer
        draft={draft}
        onDraftChange={setDraft}
        onSubmit={handleSubmit}
        placeholder={
          session?.scope?.type === "story"
            ? `Continue asking about ${session.scope.topicTitle}...`
            : "Ask anything about fashion trends, brands, or recent signals..."
        }
        statusLabel="Thinking"
        submitLabel="Send"
        submittingLabel="Sending"
        isSubmitting={isSubmitting}
        attachments={attachments}
        onAppendFiles={appendFiles}
        onRemoveAttachment={removeAttachment}
      />
    </div>
  );
}
