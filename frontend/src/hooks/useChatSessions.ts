import { useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { useAuth } from "@/hooks/useAuth";
import type { Topic } from "@/hooks/useFeedData";
import apiClient, {
  apiBaseUrl,
  getStoredAuthToken,
  resolveApiUrl,
} from "@/lib/api-client";
import {
  buildSessionDescription,
  sortChatSessions,
  type ChatAttachment,
  type ChatCitation,
  type ChatMessage,
  type ChatSession,
  type ChatUploadAttachment,
} from "@/lib/chat";

interface AttachmentResponse {
  chat_attachment_id: string;
  attachment_type: string;
  mime_type: string;
  original_filename: string;
  size_bytes: number;
  content_url: string;
}

interface MessageResponse {
  chat_message_id: string;
  role: string;
  content_text: string;
  status: string;
  response_json: Record<string, unknown> | null;
  error_message: string | null;
  attachments: AttachmentResponse[];
  created_at: string;
  completed_at: string | null;
}

interface MessageListResponse {
  messages: MessageResponse[];
}

interface SessionResponse {
  chat_session_id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

interface SessionListResponse {
  sessions: SessionResponse[];
}

interface CreateMessageResponse {
  chat_session_id: string;
  user_message_id: string;
  assistant_message_id: string;
}

interface StreamMessageStartResponse {
  chat_session_id: string;
  session_title: string;
  session_updated_at: string;
  user_message: MessageResponse;
  assistant_message: MessageResponse;
}

interface SseEvent {
  event: string;
  data: Record<string, unknown>;
}

function extractErrorMessage(error: unknown, fallback: string) {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data;
    if (typeof detail === "string" && detail.trim()) {
      return detail;
    }
    if (
      detail &&
      typeof detail === "object" &&
      "detail" in detail &&
      typeof detail.detail === "string" &&
      detail.detail.trim()
    ) {
      return detail.detail;
    }
    if (error.message.trim()) {
      return error.message;
    }
  }

  if (error instanceof Error && error.message.trim()) {
    return error.message;
  }

  return fallback;
}

function normalizeMessageStatus(
  status: string
): "done" | "queued" | "running" | "failed" {
  if (status === "queued" || status === "running" || status === "failed") {
    return status;
  }

  return "done";
}

function mapAttachment(attachment: AttachmentResponse): ChatAttachment {
  return {
    id: attachment.chat_attachment_id,
    name: attachment.original_filename,
    mimeType: attachment.mime_type,
    size: attachment.size_bytes,
    url: resolveApiUrl(attachment.content_url),
  };
}

function mapCitations(responseJson: Record<string, unknown> | null): ChatCitation[] {
  const rawCitations = responseJson?.citations;
  if (!Array.isArray(rawCitations)) {
    return [];
  }

  return rawCitations.flatMap((citation, index) => {
    if (!citation || typeof citation !== "object") {
      return [];
    }

    const href = typeof citation.url === "string" ? citation.url : "";
    if (!href) {
      return [];
    }

    const marker =
      typeof citation.marker === "string" && citation.marker.trim()
        ? citation.marker
        : `C${index + 1}`;
    const title =
      typeof citation.title === "string" && citation.title.trim()
        ? citation.title
        : "查看引用来源";
    const sourceName =
      typeof citation.source_name === "string" && citation.source_name.trim()
        ? citation.source_name
        : "引用来源";
    const snippet =
      typeof citation.snippet === "string" && citation.snippet.trim()
        ? citation.snippet
        : `${marker} · ${sourceName}`;

    return [
      {
        id: `${marker}-${href}-${index}`,
        marker,
        title,
        sourceName,
        href,
        snippet,
      },
    ];
  });
}

function mapMessage(message: MessageResponse): ChatMessage {
  return {
    id: message.chat_message_id,
    role: message.role === "assistant" ? "assistant" : "user",
    content: message.content_text,
    createdAt: message.created_at,
    status: normalizeMessageStatus(message.status),
    errorMessage: message.error_message,
    citations: mapCitations(message.response_json),
    attachments: message.attachments.map(mapAttachment),
  };
}

function mapSession(
  session: SessionResponse,
  messages: ChatMessage[]
): ChatSession {
  return {
    id: session.chat_session_id,
    title: session.title,
    description: buildSessionDescription(messages),
    updatedAt: session.updated_at,
    messages,
  };
}

function buildStoryPrompt(
  topic: Topic,
  question: string,
  attachments: ChatUploadAttachment[]
) {
  const trimmedQuestion = question.trim();
  if (trimmedQuestion) {
    return `围绕「${topic.title}」继续分析：${trimmedQuestion}`;
  }

  if (attachments.length > 0) {
    return `围绕「${topic.title}」分析我上传的 ${attachments.length} 张图片。`;
  }

  return "";
}

function appendOrReplaceSession(
  sessions: ChatSession[],
  nextSession: ChatSession
) {
  return sortChatSessions(
    sessions.filter((session) => session.id !== nextSession.id).concat(nextSession)
  );
}

function mapUploadAttachmentsToChatAttachments(
  attachments: ChatUploadAttachment[]
): ChatAttachment[] {
  return attachments.map((attachment) => ({
    id: attachment.id,
    name: attachment.name,
    mimeType: attachment.mimeType,
    size: attachment.size,
    url: attachment.previewUrl,
  }));
}

function createAssistantPlaceholderMessage(messageId: string): ChatMessage {
  return {
    id: messageId,
    role: "assistant",
    content: "",
    createdAt: new Date().toISOString(),
    status: "running",
    errorMessage: null,
    citations: [],
    attachments: [],
  };
}

function buildChatStreamEndpoint() {
  return `${apiBaseUrl}/chat/messages/stream`;
}

function buildStreamErrorMessage(status: number, body: string) {
  if (!body.trim()) {
    return `Streaming request failed with status ${status}.`;
  }
  return body.trim();
}

async function* readSseEvents(
  stream: ReadableStream<Uint8Array>
): AsyncGenerator<SseEvent> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });

    let separatorIndex = buffer.indexOf("\n\n");
    while (separatorIndex >= 0) {
      const rawEvent = buffer.slice(0, separatorIndex).trim();
      buffer = buffer.slice(separatorIndex + 2);
      separatorIndex = buffer.indexOf("\n\n");

      if (!rawEvent) {
        continue;
      }

      let eventName = "message";
      const dataLines: string[] = [];

      for (const line of rawEvent.split(/\r?\n/)) {
        if (line.startsWith("event:")) {
          eventName = line.slice("event:".length).trim();
          continue;
        }
        if (line.startsWith("data:")) {
          dataLines.push(line.slice("data:".length).trim());
        }
      }

      if (dataLines.length === 0) {
        continue;
      }

      yield {
        event: eventName,
        data: JSON.parse(dataLines.join("\n")) as Record<string, unknown>,
      };
    }

    if (done) {
      break;
    }
  }
}

export function useChatSessions() {
  const { hydrated: authHydrated, isAuthenticated } = useAuth();
  const [sessions, setSessions] = useState<ChatSession[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const isMountedRef = useRef(true);

  useEffect(() => {
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const replaceMessage = (sessionId: string, nextMessage: ChatMessage) => {
    setSessions((current) => {
      if (!current) {
        return current;
      }

      return current.map((session) => {
        if (session.id !== sessionId) {
          return session;
        }

        const nextMessages = session.messages.some(
          (message) => message.id === nextMessage.id
        )
          ? session.messages.map((message) =>
              message.id === nextMessage.id ? nextMessage : message
            )
          : [...session.messages, nextMessage];

        return {
          ...session,
          description: buildSessionDescription(nextMessages),
          messages: nextMessages,
        };
      });
    });
  };

  const loadSessionMessages = async (sessionId: string) => {
    const response = await apiClient.get<MessageListResponse>(
      `/chat/sessions/${sessionId}/messages`
    );
    return response.data.messages.map(mapMessage);
  };

  const loadSessionSnapshot = async (sessionId: string) => {
    const [sessionResponse, messages] = await Promise.all([
      apiClient.get<SessionResponse>(`/chat/sessions/${sessionId}`),
      loadSessionMessages(sessionId),
    ]);

    const nextSession = mapSession(sessionResponse.data, messages);
    setSessions((current) =>
      appendOrReplaceSession(current ?? [], nextSession)
    );
    return nextSession;
  };

  useEffect(() => {
    if (!authHydrated) {
      return;
    }

    if (!isAuthenticated) {
      setSessions([]);
      setError(null);
      return;
    }

    let cancelled = false;

    async function fetchSessions() {
      setError(null);
      setSessions(null);

      try {
        const response = await apiClient.get<SessionListResponse>("/chat/sessions");
        const loadedSessions = await Promise.all(
          response.data.sessions.map(async (session) => {
            const messages = await loadSessionMessages(session.chat_session_id);
            return mapSession(session, messages);
          })
        );

        if (!cancelled) {
          setSessions(sortChatSessions(loadedSessions));
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(extractErrorMessage(loadError, "无法加载聊天会话。"));
          setSessions([]);
        }
      }
    }

    void fetchSessions();

    return () => {
      cancelled = true;
    };
  }, [authHydrated, isAuthenticated]);

  const orderedSessions = useMemo(() => {
    return sortChatSessions(sessions ?? []);
  }, [sessions]);

  const createMessage = async (
    question: string,
    attachments: ChatUploadAttachment[],
    chatSessionId?: string
  ) => {
    const trimmedQuestion = question.trim();
    if (!trimmedQuestion && attachments.length === 0) {
      return null;
    }

    const formData = new FormData();
    if (chatSessionId) {
      formData.set("chat_session_id", chatSessionId);
    }
    if (trimmedQuestion) {
      formData.set("content_text", trimmedQuestion);
    }
    attachments.forEach((attachment) => {
      formData.append("images", attachment.file);
    });

    const response = await apiClient.post<CreateMessageResponse>(
      "/chat/messages",
      formData
    );
    return response.data;
  };

  const streamMessage = async (
    question: string,
    attachments: ChatUploadAttachment[],
    chatSessionId?: string
  ) => {
    const trimmedQuestion = question.trim();
    if (!trimmedQuestion && attachments.length === 0) {
      return null;
    }

    const token = getStoredAuthToken();
    if (!token) {
      throw new Error("Missing auth token.");
    }

    const formData = new FormData();
    if (chatSessionId) {
      formData.set("chat_session_id", chatSessionId);
    }
    if (trimmedQuestion) {
      formData.set("content_text", trimmedQuestion);
    }
    attachments.forEach((attachment) => {
      formData.append("images", attachment.file);
    });

    const response = await fetch(buildChatStreamEndpoint(), {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
      },
      body: formData,
    });
    if (!response.ok || !response.body) {
      const body = await response.text();
      throw new Error(buildStreamErrorMessage(response.status, body));
    }

    return new Promise<string | null>((resolve, reject) => {
      let didResolve = false;

      const resolveOnce = (value: string | null) => {
        if (!didResolve) {
          didResolve = true;
          resolve(value);
        }
      };

      const rejectOnce = (error: unknown) => {
        if (!didResolve) {
          didResolve = true;
          reject(error);
        }
      };

      void (async () => {
        try {
          for await (const sseEvent of readSseEvents(response.body!)) {
            if (!isMountedRef.current) {
              break;
            }

            if (sseEvent.event === "message_start") {
              const payload = sseEvent.data as unknown as StreamMessageStartResponse;
              const userMessage = mapMessage(payload.user_message);
              const assistantMessage = mapMessage(payload.assistant_message);
              const nextSessionId = payload.chat_session_id;
              const nextSession: ChatSession = {
                id: nextSessionId,
                title: payload.session_title,
                description: buildSessionDescription([
                  userMessage,
                  assistantMessage,
                ]),
                updatedAt: payload.session_updated_at,
                messages: [userMessage, assistantMessage],
              };

              setSessions((current) => {
                if (!current) {
                  return [nextSession];
                }

                const existingSession = current.find(
                  (session) => session.id === nextSessionId
                );
                if (!existingSession) {
                  return appendOrReplaceSession(current, nextSession);
                }

                const nextMessages = existingSession.messages.concat([
                  userMessage,
                  assistantMessage,
                ]);
                return appendOrReplaceSession(current, {
                  ...existingSession,
                  title: payload.session_title,
                  updatedAt: payload.session_updated_at,
                  description: buildSessionDescription(nextMessages),
                  messages: nextMessages,
                });
              });
              resolveOnce(nextSessionId);
              continue;
            }

            if (sseEvent.event === "assistant_delta") {
              const delta = typeof sseEvent.data.delta === "string"
                ? sseEvent.data.delta
                : "";
              const assistantMessageId = (() => {
                const sessionsSnapshot = sessions ?? [];
                const targetSession = chatSessionId
                  ? sessionsSnapshot.find((session) => session.id === chatSessionId)
                  : sessionsSnapshot[0];
                const lastAssistant = targetSession?.messages
                  .slice()
                  .reverse()
                  .find((message) => message.role === "assistant");
                return lastAssistant?.id;
              })();
              if (!assistantMessageId) {
                continue;
              }

              setSessions((current) => {
                if (!current) {
                  return current;
                }

                return current.map((session) => {
                  if (
                    session.id !== (chatSessionId ?? session.id)
                    && session.id !== sseEvent.data.chat_session_id
                  ) {
                    return session;
                  }

                  const nextMessages = session.messages.map((message) => {
                    if (message.id !== assistantMessageId) {
                      return message;
                    }

                    return {
                      ...message,
                      status: "running" as const,
                      content: `${message.content}${delta}`,
                    };
                  });

                  return {
                    ...session,
                    description: buildSessionDescription(nextMessages),
                    messages: nextMessages,
                  };
                });
              });
              continue;
            }

            if (
              sseEvent.event === "message_complete"
              || sseEvent.event === "message_error"
            ) {
              const nextMessage = mapMessage(
                sseEvent.data as unknown as MessageResponse
              );
              const targetSessionId = chatSessionId ?? (() => {
                const messageSession = sessions?.find((session) =>
                  session.messages.some(
                    (message) => message.id === nextMessage.id
                  )
                );
                return messageSession?.id ?? null;
              })();

              if (targetSessionId) {
                replaceMessage(targetSessionId, nextMessage);
              }

              if (sseEvent.event === "message_error") {
                toast.error("AI 回答失败，请查看会话中的错误信息。");
              }
            }
          }

          resolveOnce(chatSessionId ?? null);
        } catch (streamError) {
          rejectOnce(streamError);
        }
      })();
    });
  };

  const createSession = async (
    question: string,
    attachments: ChatUploadAttachment[] = []
  ) => {
    try {
      return await streamMessage(question, attachments);
    } catch (createError) {
      toast.error(extractErrorMessage(createError, "创建会话失败。"));
      return null;
    }
  };

  const createStorySession = async (
    topic: Topic,
    question: string,
    attachments: ChatUploadAttachment[] = []
  ) => {
    return createSession(buildStoryPrompt(topic, question, attachments), attachments);
  };

  const sendMessage = async (
    sessionId: string,
    question: string,
    attachments: ChatUploadAttachment[] = []
  ) => {
    try {
      await streamMessage(question, attachments, sessionId);
    } catch (sendError) {
      toast.error(extractErrorMessage(sendError, "发送消息失败。"));
    }
  };

  return {
    hydrated: !isAuthenticated || sessions !== null,
    error,
    sessions: orderedSessions,
    createSession,
    createStorySession,
    sendMessage,
  };
}
