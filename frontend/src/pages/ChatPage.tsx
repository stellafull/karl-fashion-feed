import { useEffect, useRef, useState } from "react";
import { useLocation } from "wouter";
import ChatAnswerContent from "@/components/ChatAnswerContent";
import { Button } from "@/components/ui/button";
import ChatComposer from "@/components/ChatComposer";
import { useImageAttachments } from "@/hooks/useImageAttachments";
import type { ChatSession, ChatUploadAttachment } from "@/lib/chat";
import {
  buildChatViewportSpacing,
  buildLastMessageScrollKey,
} from "@/lib/chat-stream";
import { cn } from "@/lib/utils";
import { formatChinaDateTimeShort } from "@/lib/time";

interface ChatPageProps {
  sessionId?: string;
  sessions: ChatSession[];
  onCreateSession: (
    question: string,
    attachments?: ChatUploadAttachment[]
  ) => Promise<string | null>;
  onSendMessage: (
    sessionId: string,
    question: string,
    attachments?: ChatUploadAttachment[]
  ) => Promise<void>;
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
  const [composerHeight, setComposerHeight] = useState(0);
  const composerRef = useRef<HTMLDivElement | null>(null);
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
  const lastMessage = session?.messages[session.messages.length - 1];
  const scrollKey = session ? buildLastMessageScrollKey(session.messages) : "empty";
  const viewportSpacing = buildChatViewportSpacing(composerHeight);

  useEffect(() => {
    const composerElement = composerRef.current;
    if (!composerElement) {
      return;
    }

    const syncComposerHeight = () => {
      setComposerHeight(Math.ceil(composerElement.getBoundingClientRect().height));
    };

    syncComposerHeight();

    const resizeObserver = new ResizeObserver(() => {
      syncComposerHeight();
    });
    resizeObserver.observe(composerElement);

    return () => {
      resizeObserver.disconnect();
    };
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({
      behavior: lastMessage?.status === "running" ? "auto" : "smooth",
      block: "end",
    });
  }, [lastMessage?.status, scrollKey]);

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
      const attachments = buildOutgoingAttachments();

      if (!sessionId) {
        const nextSessionId = await onCreateSession(question, attachments);
        if (nextSessionId) {
          setLocation(`/chat/${nextSessionId}`);
        }
        resetComposer();
        return;
      }

      await onSendMessage(sessionId, question, attachments);
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
            {session ? "Persistent backend session" : "Fashion Feed Knowledge"}
          </div>
        </div>
      </header>

      <div
        className="flex-1 overflow-y-auto overscroll-contain px-4 py-5 md:px-8"
        style={{ paddingBottom: viewportSpacing.bottomPadding }}
      >
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
          <div className="mx-auto w-full max-w-[56rem] space-y-6 py-3">
            {session.messages.map((message) => (
              <div
                key={message.id}
                className={message.role === "user" ? "flex justify-end" : "flex justify-start"}
              >
                <div
                  className={cn(
                    "w-full max-w-3xl",
                    message.role === "user"
                      ? "max-w-[46rem] rounded-[22px] bg-[#f1e8da] px-4 py-3.5 text-[#1f1c18]"
                      : "text-[#1f1c18]"
                  )}
                >
                  <div className="mb-2 text-[11px] uppercase tracking-[0.18em] text-[#8a8379]">
                    {message.role === "user" ? "You" : "Fashion Feed AI"}
                  </div>
                  <div className={message.role === "assistant" ? "" : "whitespace-pre-line text-[15px] leading-7"}>
                    {message.attachments.length > 0 && (
                      <div className="mb-2.5 grid grid-cols-2 gap-2 sm:grid-cols-3">
                        {message.attachments.map((attachment) => (
                          <div
                            key={attachment.id}
                            className="overflow-hidden rounded-[18px] border border-black/10 bg-black/5"
                          >
                            <img
                              src={attachment.url}
                              alt={attachment.name}
                              className="aspect-square w-full object-cover"
                              loading="lazy"
                            />
                          </div>
                        ))}
                      </div>
                    )}
                    {message.content && (
                      message.role === "assistant" ? (
                        <ChatAnswerContent
                          content={message.content}
                          citations={message.citations}
                        />
                      ) : (
                        <div className="whitespace-pre-line text-[15px] leading-7">
                          {message.content}
                        </div>
                      )
                    )}
                    {!message.content && message.role === "assistant" && (
                      <div className="text-[15px] leading-7 text-[#6d655b]">
                        {message.status === "failed"
                          ? message.errorMessage || "回答失败，请稍后重试。"
                          : "正在生成回答..."}
                      </div>
                    )}
                  </div>
                  <div className="mt-2.5 text-[11px] text-[#8a8379]">
                    {formatChinaDateTimeShort(message.createdAt)}
                  </div>
                </div>
              </div>
            ))}
            <div
              ref={messagesEndRef}
              style={{ scrollMarginBottom: viewportSpacing.scrollMarginBottom }}
            />
          </div>
        )}
      </div>

      <ChatComposer
        containerRef={composerRef}
        draft={draft}
        onDraftChange={setDraft}
        onSubmit={handleSubmit}
        placeholder={
          "Ask anything about fashion trends, brands, or recent signals..."
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
