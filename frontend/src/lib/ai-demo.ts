import { nanoid } from "nanoid";
import type { FeedMeta, Topic } from "@/hooks/useFeedData";

export interface AiCitation {
  id: string;
  topicId: string;
  topicTitle: string;
  sourceName: string;
  sourceTitle: string;
  href: string;
  note: string;
}

export interface AiMessage {
  id: string;
  role: "assistant" | "user";
  content: string;
  createdAt: string;
  citations?: AiCitation[];
}

export interface AiSession {
  id: string;
  title: string;
  description: string;
  updatedAt: string;
  scope?: {
    type: "story";
    topicId: string;
    topicTitle: string;
  };
  messages: AiMessage[];
}

const GLOBAL_PROMPTS = [
  "最近一周奢侈品牌联名有哪些共同点？",
  "哪些 story 在讨论同一个趋势？",
  "最近和 AI 相关的时尚 story 有哪些？",
  "哪些品牌在男装方向动作最频繁？",
];

function nowIso() {
  return new Date().toISOString();
}

function unique<T>(items: T[]) {
  return Array.from(new Set(items));
}

function normalizeText(value: string) {
  return value.toLowerCase().replace(/\s+/g, " ").trim();
}

function shorten(value: string, maxLength = 84) {
  if (value.length <= maxLength) {
    return value;
  }

  return `${value.slice(0, maxLength - 1)}…`;
}

function leadSentence(value: string) {
  const text = value.replace(/\s+/g, " ").trim();
  const match = text.match(/^(.+?[。！？.!?])/);
  return shorten(match?.[1] ?? text, 78);
}

function extractTerms(question: string) {
  const normalized = normalizeText(question);
  const parts = normalized
    .split(/[，。！？；：“”"'、\s,/()[\]-]+/)
    .map((part) => part.trim())
    .filter((part) => part.length >= 2);
  const hanTerms = normalized.match(/[\u4e00-\u9fff]{2,}/g) ?? [];

  return unique([normalized, ...parts, ...hanTerms]).slice(0, 12);
}

function scoreTopic(topic: Topic, question: string) {
  const normalizedQuestion = normalizeText(question);
  const terms = extractTerms(question);
  const title = normalizeText(topic.title);
  const summary = normalizeText(topic.summary);
  const category = normalizeText(topic.category_name);
  const tags = topic.tags.map((tag) => normalizeText(tag));
  const sources = topic.sources.map((source) => normalizeText(source.name));

  let score = 0;

  if (title.includes(normalizedQuestion) || summary.includes(normalizedQuestion)) {
    score += 10;
  }

  for (const term of terms) {
    if (title.includes(term)) {
      score += 6;
    }
    if (summary.includes(term)) {
      score += 4;
    }
    if (category.includes(term)) {
      score += 2;
    }
    if (tags.some((tag) => tag.includes(term) || term.includes(tag))) {
      score += 3;
    }
    if (sources.some((source) => source.includes(term) || term.includes(source))) {
      score += 2;
    }
  }

  if (topic.article_count > 1) {
    score += 1;
  }

  return score;
}

function rankTopics(question: string, topics: Topic[]) {
  const scored = topics
    .map((topic) => ({
      topic,
      score: scoreTopic(topic, question),
    }))
    .sort((left, right) => {
      if (right.score !== left.score) {
        return right.score - left.score;
      }

      return right.topic.published.localeCompare(left.topic.published);
    });

  if (scored.length === 0) {
    return {
      topics: [] as Topic[],
      hasDirectMatch: false,
    };
  }

  const topScore = scored[0].score;
  const hasDirectMatch = topScore > 0;

  if (!hasDirectMatch) {
    const fallback = [...topics]
      .sort((left, right) => {
        if (right.article_count !== left.article_count) {
          return right.article_count - left.article_count;
        }

        return right.published.localeCompare(left.published);
      })
      .slice(0, 3);

    return {
      topics: fallback,
      hasDirectMatch,
    };
  }

  return {
    topics: scored.slice(0, 3).map((item) => item.topic),
    hasDirectMatch,
  };
}

function buildCitation(topic: Topic, source: Topic["sources"][number], index: number): AiCitation {
  return {
    id: `${topic.id}-${index}`,
    topicId: topic.id,
    topicTitle: topic.title,
    sourceName: source.name,
    sourceTitle: source.title,
    href: source.link,
    note: `${topic.category_name} · 综合 ${topic.article_count} 篇报道`,
  };
}

function buildTopicCitations(topic: Topic, limit = 2) {
  return topic.sources
    .slice(0, limit)
    .map((source, index) => buildCitation(topic, source, index));
}

function buildWelcomeMessage(meta: FeedMeta | null): AiMessage {
  const message = meta
    ? `这里是全局 AI 入口。当前首页已加载 ${meta.total_topics} 个话题、${meta.total_articles} 篇原文。你可以先问趋势、品牌动作或跨 story 对比问题。`
    : "这里是全局 AI 入口。你可以先问趋势、品牌动作或跨 story 对比问题。";

  return {
    id: nanoid(),
    role: "assistant",
    content: `${message}\n\n当前为前端演示模式：回答基于本地 feed 数据生成，正式版本会接入 chat session、citation 和持久化 API。`,
    createdAt: nowIso(),
  };
}

export function createUserMessage(content: string): AiMessage {
  return {
    id: nanoid(),
    role: "user",
    content: content.trim(),
    createdAt: nowIso(),
  };
}

export function createEmptyAiSession(meta: FeedMeta | null): AiSession {
  const welcomeMessage = buildWelcomeMessage(meta);

  return {
    id: nanoid(),
    title: "新建会话",
    description: "从首页跨 story 提问与比较",
    updatedAt: welcomeMessage.createdAt,
    messages: [welcomeMessage],
  };
}

export function createInitialAiSessions(topics: Topic[], meta: FeedMeta | null) {
  if (topics.length === 0) {
    return [createEmptyAiSession(meta)];
  }

  const seedQuestions = [
    "最近一周奢侈品牌联名有哪些共同点？",
    "最近和 AI 相关的时尚 story 有哪些？",
  ];

  const seeded = seedQuestions.map((question) => {
    const userMessage = createUserMessage(question);
    const assistantMessage = createGlobalAssistantMessage(question, topics, meta);

    return {
      id: nanoid(),
      title: shorten(question, 16),
      description: assistantMessage.content.split("\n")[0] ?? "跨 story 快速分析",
      updatedAt: assistantMessage.createdAt,
      messages: [userMessage, assistantMessage],
    };
  });

  return [...seeded, createEmptyAiSession(meta)];
}

export function buildGlobalPrompts(topics: Topic[]) {
  const dynamicPrompts: string[] = [];
  const aiTopic = topics.find((topic) =>
    /ai/i.test(topic.title) || topic.tags.some((tag) => /ai/i.test(tag))
  );

  if (aiTopic) {
    dynamicPrompts.push(`围绕「${aiTopic.title}」还有哪些相近话题？`);
  }

  const richTopic = [...topics].sort(
    (left, right) => right.article_count - left.article_count
  )[0];
  if (richTopic) {
    dynamicPrompts.push(`帮我总结「${richTopic.title}」对应的行业信号。`);
  }

  return unique([...dynamicPrompts, ...GLOBAL_PROMPTS]).slice(0, 4);
}

export function buildStoryPrompts(topic: Topic) {
  const primaryTag = topic.tags[0];

  return unique([
    "这条消息对品牌经营意味着什么？",
    "它和最近哪些趋势相似？",
    "如果继续跟踪这个话题，应该优先关注哪些来源？",
    primaryTag ? `${primaryTag} 相关 story 里还有哪些可比案例？` : "",
  ]).filter(Boolean);
}

export function createGlobalAssistantMessage(
  question: string,
  topics: Topic[],
  meta: FeedMeta | null
): AiMessage {
  const ranked = rankTopics(question, topics);
  const intro = meta
    ? `我先基于当前首页已加载的 ${meta.total_topics} 个话题做一次快速梳理。`
    : "我先基于当前首页已加载的话题做一次快速梳理。";
  const matchLine = ranked.hasDirectMatch
    ? "和你问题最接近的信号主要集中在这几条 story："
    : "没有命中非常直接的 story，我先返回当前最值得优先看的话题：";
  const detailLines = ranked.topics.map(
    (topic, index) => `${index + 1}. ${topic.title}：${leadSentence(topic.summary)}`
  );
  const hintLine = ranked.hasDirectMatch
    ? "如果你想继续收窄范围，可以补充品牌名、时间范围或品类。"
    : "你可以继续补充品牌名、品类或时间范围，我会把范围收得更窄。";

  return {
    id: nanoid(),
    role: "assistant",
    createdAt: nowIso(),
    content: [
      intro,
      matchLine,
      ...detailLines,
      hintLine,
      "当前为前端演示模式：回答只使用已加载的 feed 数据，正式版本会接入检索、session 持久化与 citation API。",
    ].join("\n"),
    citations: ranked.topics.flatMap((topic) => buildTopicCitations(topic, 1)),
  };
}

export function createStoryAssistantMessage(question: string, topic: Topic): AiMessage {
  const signals = [
    leadSentence(topic.summary),
    ...topic.key_points.slice(0, 2).map((item) => shorten(item, 60)),
  ].filter(Boolean);
  const tagLine =
    topic.tags.length > 0
      ? `后续可以重点对比这些标签：${topic.tags.slice(0, 3).join(" / ")}。`
      : "后续可以重点对比相近品牌动作、来源变化和时间线。";

  return {
    id: nanoid(),
    role: "assistant",
    createdAt: nowIso(),
    content: [
      `围绕「${topic.title}」，我会先把你的问题放回当前 story 的上下文里理解。`,
      `你当前追问的是：${question}`,
      `1. 这条 story 的核心信号是：${signals[0] ?? "当前摘要信息较少，建议优先回看来源原文。"}`,
      signals[1] ? `2. 当前最直接的佐证线索包括：${signals.slice(1).join("；")}` : "",
      `3. ${tagLine}`,
      "当前为前端演示模式：这里已经自动带入该 story 的摘要、标签和来源；正式版本会补上检索增强、citation 回溯和会话持久化。",
    ]
      .filter(Boolean)
      .join("\n"),
    citations: buildTopicCitations(topic, 3),
  };
}
