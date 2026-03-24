import { useEffect, useMemo, useState } from "react";
import { nanoid } from "nanoid";
import type { FeedMeta, Topic } from "@/hooks/useFeedData";
import {
  type AiAttachment,
  type AiSession,
  createGlobalAssistantMessage,
  createInitialAiSessions,
  createStoryAssistantMessage,
  createUserMessage,
} from "@/lib/ai-demo";

const STORAGE_KEY = "fashion-feed-chat-sessions-v2";

function buildSessionTitle(question: string) {
  const normalized = question.trim().replace(/\s+/g, " ");
  if (!normalized) {
    return "新建会话";
  }

  return normalized.length > 22 ? `${normalized.slice(0, 21)}…` : normalized;
}

function buildQuestionPayload(question: string, attachments: AiAttachment[] = []) {
  const trimmedQuestion = question.trim();
  if (trimmedQuestion) {
    return trimmedQuestion;
  }

  if (attachments.length > 0) {
    return `请分析我上传的 ${attachments.length} 张图片。`;
  }

  return "";
}

function parseStoredSessions(rawValue: string | null) {
  if (!rawValue) {
    return null;
  }

  try {
    const parsed = JSON.parse(rawValue);
    if (!Array.isArray(parsed)) {
      return null;
    }

    return parsed as AiSession[];
  } catch {
    return null;
  }
}

export function useChatSessions(topics: Topic[], meta: FeedMeta | null) {
  const [sessions, setSessions] = useState<AiSession[] | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") {
      setSessions([]);
      return;
    }

    const stored = parseStoredSessions(window.localStorage.getItem(STORAGE_KEY));
    setSessions(stored ?? []);
  }, []);

  useEffect(() => {
    if (sessions === null || sessions.length > 0 || topics.length === 0) {
      return;
    }

    setSessions(createInitialAiSessions(topics, meta));
  }, [meta, sessions, topics]);

  useEffect(() => {
    if (sessions === null || typeof window === "undefined") {
      return;
    }

    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions));
  }, [sessions]);

  const orderedSessions = useMemo(() => {
    return [...(sessions ?? [])].sort((left, right) =>
      right.updatedAt.localeCompare(left.updatedAt)
    );
  }, [sessions]);

  const createSession = (question: string, attachments: AiAttachment[] = []) => {
    const prompt = buildQuestionPayload(question, attachments);
    if (!prompt) {
      return null;
    }

    const userMessage = createUserMessage(question, attachments);
    const assistantMessage = createGlobalAssistantMessage(prompt, topics, meta);
    const nextSession: AiSession = {
      id: nanoid(),
      title: buildSessionTitle(prompt),
      description: assistantMessage.content.split("\n")[0] ?? "跨 story 快速分析",
      updatedAt: assistantMessage.createdAt,
      messages: [userMessage, assistantMessage],
    };

    setSessions((current) => [nextSession, ...(current ?? [])]);
    return nextSession.id;
  };

  const createStorySession = (
    topic: Topic,
    question: string,
    attachments: AiAttachment[] = []
  ) => {
    const prompt = buildQuestionPayload(question, attachments);
    if (!prompt) {
      return null;
    }

    const userMessage = createUserMessage(question, attachments);
    const assistantMessage = createStoryAssistantMessage(prompt, topic);
    const nextSession: AiSession = {
      id: nanoid(),
      title: buildSessionTitle(prompt),
      description: assistantMessage.content.split("\n")[0] ?? `围绕 ${topic.title} 继续追问`,
      updatedAt: assistantMessage.createdAt,
      scope: {
        type: "story",
        topicId: topic.id,
        topicTitle: topic.title,
      },
      messages: [userMessage, assistantMessage],
    };

    setSessions((current) => [nextSession, ...(current ?? [])]);
    return nextSession.id;
  };

  const sendMessage = (
    sessionId: string,
    question: string,
    attachments: AiAttachment[] = []
  ) => {
    const prompt = buildQuestionPayload(question, attachments);
    if (!prompt) {
      return;
    }

    setSessions((current) =>
      (current ?? []).map((session) => {
        if (session.id !== sessionId) {
          return session;
        }

        const userMessage = createUserMessage(question, attachments);
        const storyScope = session.scope?.type === "story" ? session.scope : null;
        const storyTopic = storyScope
          ? topics.find((topic) => topic.id === storyScope.topicId) ?? null
          : null;
        const assistantMessage = storyTopic
          ? createStoryAssistantMessage(prompt, storyTopic)
          : createGlobalAssistantMessage(prompt, topics, meta);

        return {
          ...session,
          updatedAt: assistantMessage.createdAt,
          description: assistantMessage.content.split("\n")[0] ?? session.description,
          title:
            session.title === "新建会话"
              ? buildSessionTitle(prompt)
              : session.title,
          messages: [...session.messages, userMessage, assistantMessage],
        };
      })
    );
  };

  const getSession = (sessionId: string | undefined) => {
    if (!sessionId) {
      return null;
    }

    return orderedSessions.find((session) => session.id === sessionId) ?? null;
  };

  return {
    hydrated: sessions !== null,
    sessions: orderedSessions,
    createSession,
    createStorySession,
    sendMessage,
    getSession,
  };
}
