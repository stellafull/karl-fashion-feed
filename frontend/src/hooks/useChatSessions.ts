import { useEffect, useMemo, useState } from "react";
import { nanoid } from "nanoid";
import type { FeedMeta, Topic } from "@/hooks/useFeedData";
import {
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

  const createSession = (question: string) => {
    const trimmedQuestion = question.trim();
    if (!trimmedQuestion) {
      return null;
    }

    const userMessage = createUserMessage(trimmedQuestion);
    const assistantMessage = createGlobalAssistantMessage(trimmedQuestion, topics, meta);
    const nextSession: AiSession = {
      id: nanoid(),
      title: buildSessionTitle(trimmedQuestion),
      description: assistantMessage.content.split("\n")[0] ?? "跨 story 快速分析",
      updatedAt: assistantMessage.createdAt,
      messages: [userMessage, assistantMessage],
    };

    setSessions((current) => [nextSession, ...(current ?? [])]);
    return nextSession.id;
  };

  const createStorySession = (topic: Topic, question: string) => {
    const trimmedQuestion = question.trim();
    if (!trimmedQuestion) {
      return null;
    }

    const userMessage = createUserMessage(trimmedQuestion);
    const assistantMessage = createStoryAssistantMessage(trimmedQuestion, topic);
    const nextSession: AiSession = {
      id: nanoid(),
      title: buildSessionTitle(trimmedQuestion),
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

  const sendMessage = (sessionId: string, question: string) => {
    const trimmedQuestion = question.trim();
    if (!trimmedQuestion) {
      return;
    }

    setSessions((current) =>
      (current ?? []).map((session) => {
        if (session.id !== sessionId) {
          return session;
        }

        const userMessage = createUserMessage(trimmedQuestion);
        const storyScope = session.scope?.type === "story" ? session.scope : null;
        const storyTopic = storyScope
          ? topics.find((topic) => topic.id === storyScope.topicId) ?? null
          : null;
        const assistantMessage = storyTopic
          ? createStoryAssistantMessage(trimmedQuestion, storyTopic)
          : createGlobalAssistantMessage(trimmedQuestion, topics, meta);

        return {
          ...session,
          updatedAt: assistantMessage.createdAt,
          description: assistantMessage.content.split("\n")[0] ?? session.description,
          title:
            session.title === "新建会话"
              ? buildSessionTitle(trimmedQuestion)
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
