import { useState } from "react";
import { ArrowLeft, ArrowUpRight, Clock3, Layers3, Share2 } from "lucide-react";
import { useLocation } from "wouter";
import { Button } from "@/components/ui/button";
import ChatComposer from "@/components/ChatComposer";
import type { Topic } from "@/hooks/useFeedData";
import { getLangLabel } from "@/hooks/useFeedData";
import { useImageAttachments } from "@/hooks/useImageAttachments";
import type { AiAttachment } from "@/lib/ai-demo";
import { formatChinaDateTimeShort } from "@/lib/time";

interface StoryPageProps {
  topic: Topic | null;
  onStartStoryChat: (
    topic: Topic,
    question: string,
    attachments?: AiAttachment[]
  ) => string | null;
}

function getUniqueSources(topic: Topic) {
  return topic.sources
    .map((source) => source.name)
    .filter((value, index, array) => array.indexOf(value) === index);
}

export default function StoryPage({ topic, onStartStoryChat }: StoryPageProps) {
  const [, setLocation] = useLocation();
  const [draft, setDraft] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const {
    attachments,
    appendFiles,
    removeAttachment,
    buildOutgoingAttachments,
    resetAttachments,
  } = useImageAttachments();

  if (!topic) {
    return (
      <div className="flex h-full min-h-0 items-center justify-center bg-[#f7f3eb] px-6 text-center">
        <div className="max-w-md space-y-4">
          <p className="font-display text-4xl text-[#2b241d]">Story not found</p>
          <p className="text-base leading-7 text-[#675f56]">
            这个 story 在当前 feed 中不存在，可能已经被移除或替换。
          </p>
          <Button onClick={() => setLocation("/discover")}>Back to discover</Button>
        </div>
      </div>
    );
  }

  const uniqueSources = getUniqueSources(topic);

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
      const sessionId = onStartStoryChat(topic, question, attachments);
      if (sessionId) {
        setLocation(`/chat/${sessionId}`);
      }
      resetComposer();
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="relative flex h-full min-h-0 flex-col bg-[#f7f3eb] text-[#1f1c18]">
      <header className="shrink-0 border-b border-[#e4dccf] bg-[#f7f3eb]/90 backdrop-blur">
        <div className="flex h-16 items-center justify-between px-5 md:px-8">
          <Button
            variant="ghost"
            className="rounded-full px-3"
            onClick={() => setLocation("/discover")}
          >
            <ArrowLeft className="h-4 w-4" />
            Discover
          </Button>
          <Button variant="outline" className="rounded-full bg-white">
            <Share2 className="h-4 w-4" />
            Share
          </Button>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto pb-48 md:pb-56">
        <article className="mx-auto max-w-5xl px-5 py-8 md:px-8">
          <div className="max-w-4xl">
            <p className="text-sm font-medium text-[#7e776d]">Discover</p>
            <h1 className="mt-6 font-display text-5xl leading-[1.02] text-[#2b241d] md:text-6xl">
              {topic.title}
            </h1>
            <div className="mt-6 flex flex-wrap items-center gap-5 text-sm text-[#726a60]">
              <span className="flex items-center gap-1.5">
                <Clock3 className="h-4 w-4 text-[#9f7d45]" />
                Published {formatChinaDateTimeShort(topic.published)}
              </span>
              <span className="flex items-center gap-1.5">
                <Layers3 className="h-4 w-4 text-[#9f7d45]" />
                {topic.article_count} sources
              </span>
              <span>{uniqueSources.join(" · ")}</span>
            </div>
            <p className="mt-6 max-w-[60ch] text-[1.45rem] leading-[1.8] text-[#575046]">
              {topic.summary}
            </p>
          </div>

          <div className="mt-8 flex justify-center">
            {topic.image ? (
              <div className="overflow-hidden rounded-[32px]">
                <img
                  src={topic.image}
                  alt=""
                  className="block h-auto w-auto max-h-[75vh] max-w-full object-contain"
                  loading="lazy"
                  decoding="async"
                />
              </div>
            ) : (
              <div className="h-[420px] w-full rounded-[32px] bg-[radial-gradient(circle_at_top,#f2ebdd,transparent_42%),linear-gradient(135deg,#ebe3d5,#d6c2a5)]" />
            )}
          </div>

          {topic.key_points.length > 0 && (
            <section className="mt-10 max-w-4xl">
              <p className="text-xs uppercase tracking-[0.24em] text-[#8a8378]">Key signals</p>
              <div className="mt-4 grid gap-3 md:grid-cols-2">
                {topic.key_points.map((point) => (
                  <div
                    key={point}
                    className="rounded-2xl border border-[#e4dccf] bg-white px-4 py-4 text-sm leading-7 text-[#5c554b]"
                  >
                    {point}
                  </div>
                ))}
              </div>
            </section>
          )}

          {topic.tags.length > 0 && (
            <section className="mt-10 max-w-4xl">
              <p className="text-xs uppercase tracking-[0.24em] text-[#8a8378]">Tags</p>
              <div className="mt-4 flex flex-wrap gap-2">
                {topic.tags.map((tag) => (
                  <span
                    key={tag}
                    className="rounded-full border border-[#ddd4c7] bg-white px-3 py-1.5 text-sm text-[#685f54]"
                  >
                    {tag}
                  </span>
                ))}
              </div>
            </section>
          )}

          <section className="mt-10 max-w-4xl">
            <p className="text-xs uppercase tracking-[0.24em] text-[#8a8378]">Source list</p>
            <div className="mt-4 space-y-3">
              {topic.sources.map((source) => (
                <a
                  key={source.link}
                  href={source.link}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-start justify-between gap-4 rounded-2xl border border-[#e4dccf] bg-white px-4 py-4 transition-colors hover:border-[#c8b18a]"
                >
                  <div className="min-w-0">
                    <p className="text-xs uppercase tracking-[0.12em] text-[#8a8378]">
                      {source.name} · {getLangLabel(source.lang)}
                    </p>
                    <p className="mt-2 text-sm leading-7 text-[#2b241d]">{source.title}</p>
                  </div>
                  <ArrowUpRight className="mt-1 h-4 w-4 shrink-0 text-[#9f7d45]" />
                </a>
              ))}
            </div>
          </section>
        </article>
      </div>

      <ChatComposer
        draft={draft}
        onDraftChange={setDraft}
        onSubmit={handleSubmit}
        placeholder="Ask follow-up about this story..."
        statusLabel="Story follow-up"
        submitLabel="Ask"
        submittingLabel="Sending"
        isSubmitting={isSubmitting}
        attachments={attachments}
        onAppendFiles={appendFiles}
        onRemoveAttachment={removeAttachment}
        shellClassName="max-w-3xl border-[#ddd4c7]/70 bg-[rgba(255,255,255,0.82)] shadow-[0_24px_80px_rgba(44,33,16,0.12)]"
        submitButtonClassName="bg-[#d2b07a] text-[#1f1c18] hover:bg-[#c6a166]"
        className="pb-4 pt-2 md:pb-6"
      />
    </div>
  );
}
