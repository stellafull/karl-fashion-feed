import { useEffect, useRef, useState } from "react";
import { ArrowUpRight, Mic, Sparkles } from "lucide-react";
import { useLocation } from "wouter";
import type { AiSession } from "@/lib/ai-demo";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { formatChinaDateTimeShort } from "@/lib/time";

interface ChatPageProps {
  sessionId?: string;
  sessions: AiSession[];
  onCreateSession: (question: string) => string | null;
  onSendMessage: (sessionId: string, question: string) => void;
}

export default function ChatPage({
  sessionId,
  sessions,
  onCreateSession,
  onSendMessage,
}: ChatPageProps) {
  const [, setLocation] = useLocation();
  const [draft, setDraft] = useState("");
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const session = sessionId
    ? sessions.find((item) => item.id === sessionId) ?? null
    : null;

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [session?.messages.length]);

  const handleSubmit = () => {
    const question = draft.trim();
    if (!question) {
      return;
    }

    if (!sessionId) {
      const nextSessionId = onCreateSession(question);
      if (nextSessionId) {
        setLocation(`/chat/${nextSessionId}`);
      }
      setDraft("");
      return;
    }

    onSendMessage(sessionId, question);
    setDraft("");
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
    <div className="flex h-full min-h-0 flex-col bg-[#f7f3eb] text-[#1f1c18]">
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

      <div className="flex-1 overflow-y-auto px-4 py-6 md:px-8">
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
          <div className="mx-auto w-full max-w-4xl space-y-6 py-4">
            {session.messages.map((message) => (
              <div
                key={message.id}
                className={message.role === "user" ? "flex justify-end" : "flex justify-start"}
              >
                <div
                  className={`max-w-[88%] rounded-[28px] px-5 py-4 ${
                    message.role === "user"
                      ? "bg-[#1f1c18] text-[#f4efe5]"
                      : "border border-[#e4dccf] bg-white text-[#1f1c18]"
                  }`}
                >
                  <div className="whitespace-pre-line text-[15px] leading-8">
                    {message.content}
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

      <div className="shrink-0 border-t border-[#e4dccf] bg-[#f7f3eb]/95 px-4 py-4 backdrop-blur md:px-8">
        <div className="mx-auto max-w-4xl">
          <div className="rounded-[32px] border border-[#ddd4c7] bg-white p-3 shadow-[0_18px_50px_rgba(44,33,16,0.05)]">
            <Textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  handleSubmit();
                }
              }}
              placeholder={
                session?.scope?.type === "story"
                  ? `Continue asking about ${session.scope.topicTitle}...`
                  : "Ask anything about fashion trends, brands, or recent signals..."
              }
              className="min-h-24 resize-none border-none bg-transparent px-3 py-3 text-base shadow-none focus-visible:ring-0"
            />
            <div className="flex items-center justify-between gap-3 px-2 pb-1 pt-2">
              <div className="flex items-center gap-2 text-sm text-[#7a7369]">
                <span className="rounded-full bg-[#f4efe5] px-3 py-1">Thinking</span>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  className="flex h-10 w-10 items-center justify-center rounded-full border border-[#ddd4c7] text-[#7a7369]"
                  aria-label="Voice input"
                >
                  <Mic className="h-4 w-4" />
                </button>
                <Button className="rounded-full bg-[#1f1c18] px-5" onClick={handleSubmit}>
                  <Sparkles className="h-4 w-4" />
                  Send
                </Button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
