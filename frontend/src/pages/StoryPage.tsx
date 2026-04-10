import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, ArrowUpRight, Clock3, Layers3, Share2 } from "lucide-react";
import { useLocation } from "wouter";
import { Button } from "@/components/ui/button";
import ChatComposer from "@/components/ChatComposer";
import ChatAnswerContent from "@/components/ChatAnswerContent";
import type { Topic } from "@/hooks/useFeedData";
import { getLangLabel } from "@/hooks/useFeedData";
import { useImageAttachments } from "@/hooks/useImageAttachments";
import apiClient from "@/lib/api-client";
import type { ChatUploadAttachment, StoryChatContext } from "@/lib/chat";
import { formatChinaDateTimeShort } from "@/lib/time";

interface StoryPageProps {
  storyId: string;
  topic: Topic | null;
  onStartStoryChat: (
    question: string,
    attachments?: ChatUploadAttachment[],
    storyContext?: StoryChatContext
  ) => Promise<string | null>;
  onStartStoryDeepResearch: (
    question: string,
    attachments?: ChatUploadAttachment[],
    storyContext?: StoryChatContext
  ) => Promise<string | null>;
}

interface DigestDetailSource {
  name: string;
  title: string;
  link: string;
  lang: string;
}

interface DigestDetailResponse {
  id: string;
  facet: string;
  title: string;
  dek: string;
  body_markdown: string;
  hero_image: string;
  published: string;
  sources: DigestDetailSource[];
}

interface StoryDetail extends Topic {
  bodyMarkdown: string;
}

function getUniqueSources(topic: Topic) {
  return topic.sources
    .map((source) => source.name)
    .filter((value, index, array) => array.indexOf(value) === index);
}

function mapDigestDetailToStoryDetail(
  payload: DigestDetailResponse,
  fallbackTopic: Topic | null
): StoryDetail {
  return {
    id: payload.id,
    title: payload.title,
    summary: payload.dek,
    key_points:
      fallbackTopic?.key_points ?? [`综合 ${payload.sources.length} 个来源的专题正文`],
    tags: fallbackTopic?.tags ?? [],
    category: fallbackTopic?.category ?? payload.facet,
    category_name: fallbackTopic?.category_name ?? payload.facet,
    image: payload.hero_image || fallbackTopic?.image || "",
    published: payload.published,
    article_count: fallbackTopic?.article_count ?? payload.sources.length,
    sources: payload.sources,
    bodyMarkdown: payload.body_markdown,
  };
}

function buildStoryChatContext(topic: StoryDetail | Topic): StoryChatContext {
  return {
    title: topic.title,
    summary: topic.summary,
    keyPoints: topic.key_points,
    bodyMarkdown: "bodyMarkdown" in topic ? topic.bodyMarkdown : undefined,
    sourceNames: topic.sources.map(source => source.name),
  };
}

export default function StoryPage({
  storyId,
  topic,
  onStartStoryChat,
  onStartStoryDeepResearch,
}: StoryPageProps) {
  const [, setLocation] = useLocation();
  const [draft, setDraft] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isDeepResearchMode, setIsDeepResearchMode] = useState(false);
  const [detail, setDetail] = useState<StoryDetail | null>(null);
  const [isLoadingDetail, setIsLoadingDetail] = useState(true);
  const [detailError, setDetailError] = useState<string | null>(null);
  const {
    attachments,
    appendFiles,
    removeAttachment,
    buildOutgoingAttachments,
    resetAttachments,
  } = useImageAttachments();

  useEffect(() => {
    let cancelled = false;

    async function fetchDetail() {
      try {
        setIsLoadingDetail(true);
        setDetailError(null);
        const response = await apiClient.get<DigestDetailResponse>(`/digests/${storyId}`);
        if (cancelled) {
          return;
        }
        setDetail(mapDigestDetailToStoryDetail(response.data, topic));
      } catch (error) {
        if (cancelled) {
          return;
        }
        setDetail(null);
        setDetailError(error instanceof Error ? error.message : "Unknown error");
      } finally {
        if (!cancelled) {
          setIsLoadingDetail(false);
        }
      }
    }

    fetchDetail();

    return () => {
      cancelled = true;
    };
  }, [storyId, topic]);

  const resolvedTopic = useMemo(() => detail ?? topic, [detail, topic]);

  if (isLoadingDetail && !resolvedTopic) {
    return (
      <div className="flex h-full min-h-0 items-center justify-center bg-[#f7f3eb] px-6 text-center">
        <div className="max-w-md space-y-4">
          <p className="font-display text-4xl text-[#2b241d]">正在加载专题</p>
          <p className="text-base leading-7 text-[#675f56]">
            正在请求正文与来源，请稍候。
          </p>
        </div>
      </div>
    );
  }

  if (!resolvedTopic) {
    return (
      <div className="flex h-full min-h-0 items-center justify-center bg-[#f7f3eb] px-6 text-center">
        <div className="max-w-md space-y-4">
          <p className="font-display text-4xl text-[#2b241d]">专题不存在</p>
          <p className="text-base leading-7 text-[#675f56]">
            {detailError || "这个专题在当前列表中不存在，可能已经被移除或替换。"}
          </p>
          <Button onClick={() => setLocation("/discover")}>返回总览</Button>
        </div>
      </div>
    );
  }

  const resolvedSources = detail?.sources ?? [];
  const uniqueSources = detail ? getUniqueSources(detail) : getUniqueSources(resolvedTopic);

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
      const storyContext = buildStoryChatContext(resolvedTopic);
      const sessionId = isDeepResearchMode
        ? await onStartStoryDeepResearch(question, attachments, storyContext)
        : await onStartStoryChat(question, attachments, storyContext);
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
            返回总览
          </Button>
          <Button variant="outline" className="rounded-full bg-white">
            <Share2 className="h-4 w-4" />
            分享
          </Button>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto pb-48 md:pb-56">
        <article className="mx-auto max-w-5xl px-5 py-8 md:px-8">
          <div className="max-w-4xl">
            <p className="text-sm font-medium text-[#7e776d]">专题详情</p>
            <h1 className="mt-6 font-display text-5xl leading-[1.02] text-[#2b241d] md:text-6xl">
              {resolvedTopic.title}
            </h1>
            <div className="mt-6 flex flex-wrap items-center gap-5 text-sm text-[#726a60]">
              <span className="flex items-center gap-1.5">
                <Clock3 className="h-4 w-4 text-[#9f7d45]" />
                发布于 {formatChinaDateTimeShort(resolvedTopic.published)}
              </span>
              <span className="flex items-center gap-1.5">
                <Layers3 className="h-4 w-4 text-[#9f7d45]" />
                {resolvedTopic.article_count} 篇原文
              </span>
              <span>{uniqueSources.join(" · ")}</span>
            </div>
            <p className="mt-6 max-w-[60ch] text-[1.45rem] leading-[1.8] text-[#575046]">
              {resolvedTopic.summary}
            </p>
          </div>

          <div className="mt-8 flex justify-center">
            {resolvedTopic.image ? (
              <div className="overflow-hidden rounded-[32px]">
                <img
                  src={resolvedTopic.image}
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

          {detail?.bodyMarkdown ? (
            <section className="mt-10 max-w-4xl">
              <ChatAnswerContent content={detail.bodyMarkdown} citations={[]} />
            </section>
          ) : null}

          {resolvedSources.length > 0 && (
            <section className="mt-10 max-w-4xl">
              <p className="text-xs uppercase tracking-[0.24em] text-[#8a8378]">来源列表</p>
              <div className="mt-4 space-y-3">
                {resolvedSources.map((source) => (
                  <a
                    key={source.link}
                    href={source.link}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-start justify-between gap-4 rounded-2xl border border-[#e4dccf] bg-white px-4 py-4 transition-colors hover:border-[#c8b18a]"
                  >
                    <div className="min-w-0">
                      <p className="text-xs uppercase tracking-[0.12em] text-[#8a8378]">
                        {source.name}
                        {source.lang ? ` · ${getLangLabel(source.lang)}` : ""}
                      </p>
                      <p className="mt-2 text-sm leading-7 text-[#2b241d]">{source.title}</p>
                    </div>
                    <ArrowUpRight className="mt-1 h-4 w-4 shrink-0 text-[#9f7d45]" />
                  </a>
                ))}
              </div>
            </section>
          )}
        </article>
      </div>

      <ChatComposer
        draft={draft}
        onDraftChange={setDraft}
        onSubmit={handleSubmit}
        placeholder="继续追问这篇专题的细节、品牌或搭配..."
        statusLabel={isDeepResearchMode ? "深度研究模式" : "专题追问"}
        submitLabel="提问"
        submittingLabel="发送中"
        isSubmitting={isSubmitting}
        attachments={attachments}
        onAppendFiles={appendFiles}
        onRemoveAttachment={removeAttachment}
        isDeepResearchMode={isDeepResearchMode}
        onToggleDeepResearch={() =>
          setIsDeepResearchMode((current) => !current)
        }
        shellClassName="max-w-3xl border-[#ddd4c7]/70 bg-[rgba(255,255,255,0.82)] shadow-[0_24px_80px_rgba(44,33,16,0.12)]"
        submitButtonClassName="bg-[#d2b07a] text-[#1f1c18] hover:bg-[#c6a166]"
        className="pb-4 pt-2 md:pb-6"
      />
    </div>
  );
}
