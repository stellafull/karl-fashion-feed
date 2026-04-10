import { useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { useAuth } from "@/hooks/useAuth";
import type { Topic } from "@/hooks/useFeedData";
import apiClient, { apiBaseUrl, getStoredAuthToken } from "@/lib/api-client";
import {
  buildSessionDescription,
  findPendingDeepResearchInterrupt,
  mapAssistantImageResults,
  sortChatSessions,
  type ChatAttachment,
  type ChatAssistantImageResult,
  type ChatCitation,
  type ChatMessage,
  type ChatSession,
  type StoryChatContext,
  type ChatUploadAttachment,
} from "@/lib/chat";
import { mapAttachmentResponse } from "@/lib/chat-attachments";
import {
  appendAssistantDeltaToSessions,
  interruptAssistantMessageInSessions,
} from "@/lib/chat-stream";

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

interface ActiveStreamState {
  controller: AbortController;
  assistantMessageId: string | null;
  sessionId: string | null;
  interrupted: boolean;
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
): ChatMessage["status"] {
  if (
    status === "queued" ||
    status === "running" ||
    status === "interrupted" ||
    status === "failed"
  ) {
    return status;
  }

  return "done";
}

function isAbortError(error: unknown) {
  return (
    (error instanceof DOMException && error.name === "AbortError") ||
    (error instanceof Error && error.name === "AbortError")
  );
}

function mapCitations(
  responseJson: Record<string, unknown> | null
): ChatCitation[] {
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

async function mapMessage(message: MessageResponse): Promise<ChatMessage> {
  return {
    id: message.chat_message_id,
    role: message.role === "assistant" ? "assistant" : "user",
    content: message.content_text,
    createdAt: message.created_at,
    status: normalizeMessageStatus(message.status),
    errorMessage: message.error_message,
    responseJson: message.response_json,
    citations: mapCitations(message.response_json),
    imageResults: mapAssistantImageResults(
      message.response_json
    ) as ChatAssistantImageResult[],
    attachments: await Promise.all(
      message.attachments.map(mapAttachmentResponse)
    ),
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

function appendOrReplaceSession(
  sessions: ChatSession[],
  nextSession: ChatSession
) {
  return sortChatSessions(
    sessions
      .filter(session => session.id !== nextSession.id)
      .concat(nextSession)
  );
}

function mapUploadAttachmentsToChatAttachments(
  attachments: ChatUploadAttachment[]
): ChatAttachment[] {
  return attachments.map(attachment => ({
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
    responseJson: null,
    citations: [],
    imageResults: [],
    attachments: [],
  };
}

function buildChatStreamEndpoint() {
  return `${apiBaseUrl}/chat/messages/stream`;
}

function buildDeepResearchStreamEndpoint() {
  return `${apiBaseUrl}/deep-research/messages/stream`;
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
  const activeStreamRef = useRef<ActiveStreamState | null>(null);

  useEffect(() => {
    return () => {
      isMountedRef.current = false;
      if (activeStreamRef.current) {
        activeStreamRef.current.interrupted = true;
        activeStreamRef.current.controller.abort();
      }
      activeStreamRef.current = null;
    };
  }, []);

  const replaceMessage = (sessionId: string, nextMessage: ChatMessage) => {
    setSessions(current => {
      if (!current) {
        return current;
      }

      return current.map(session => {
        if (session.id !== sessionId) {
          return session;
        }

        const nextMessages = session.messages.some(
          message => message.id === nextMessage.id
        )
          ? session.messages.map(message =>
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

  const patchMessageResponseJson = (
    sessionId: string,
    messageId: string,
    patch: Record<string, unknown>
  ) => {
    setSessions(current => {
      if (!current) {
        return current;
      }

      return current.map(session => {
        if (session.id !== sessionId) {
          return session;
        }

        const nextMessages = session.messages.map(message => {
          if (message.id !== messageId) {
            return message;
          }

          return {
            ...message,
            responseJson: {
              ...(message.responseJson ?? {}),
              ...patch,
            },
          };
        });

        return {
          ...session,
          description: buildSessionDescription(nextMessages),
          messages: nextMessages,
        };
      });
    });
  };

  const markMessageTerminalError = (
    sessionId: string,
    assistantMessageId: string,
    errorMessage: string
  ) => {
    setSessions(current => {
      if (!current) {
        return current;
      }

      return current.map(session => {
        if (session.id !== sessionId) {
          return session;
        }

        const nextMessages = session.messages.map(message => {
          if (message.id !== assistantMessageId) {
            return message;
          }

          return {
            ...message,
            status: "failed" as const,
            errorMessage,
          };
        });

        return {
          ...session,
          description: buildSessionDescription(nextMessages),
          messages: nextMessages,
        };
      });
    });
  };

  const finishActiveStream = (streamState: ActiveStreamState) => {
    if (activeStreamRef.current === streamState) {
      activeStreamRef.current = null;
    }
  };

  const interruptMessage = async (
    sessionId: string,
    assistantMessageId: string
  ) => {
    const activeStream = activeStreamRef.current;
    const isActiveStreamTarget =
      activeStream?.sessionId === sessionId &&
      activeStream.assistantMessageId === assistantMessageId;

    if (isActiveStreamTarget && activeStream.interrupted) {
      return;
    }

    if (isActiveStreamTarget) {
      activeStream.interrupted = true;
    }
    setSessions(current => {
      if (!current) {
        return current;
      }

      return interruptAssistantMessageInSessions(
        current,
        sessionId,
        assistantMessageId
      );
    });

    if (isActiveStreamTarget) {
      activeStream.controller.abort();
      finishActiveStream(activeStream);
    }

    try {
      const response = await apiClient.post<MessageResponse>(
        `/chat/messages/${assistantMessageId}/interrupt`
      );
      const nextMessage = await mapMessage(response.data);
      replaceMessage(sessionId, nextMessage);
    } catch (interruptError) {
      if (isActiveStreamTarget) {
        activeStream.interrupted = false;
      }
      toast.error(extractErrorMessage(interruptError, "停止生成失败。"));
      void loadSessionSnapshot(sessionId);
    }
  };

  const canInterruptMessage = (
    sessionId: string,
    assistantMessageId: string
  ) => {
    const activeStream = activeStreamRef.current;
    if (!activeStream) {
      return true;
    }

    if (
      activeStream.sessionId !== sessionId ||
      activeStream.assistantMessageId !== assistantMessageId
    ) {
      return true;
    }

    return !activeStream.interrupted;
  };

  const loadSessionMessages = async (sessionId: string) => {
    const response = await apiClient.get<MessageListResponse>(
      `/chat/sessions/${sessionId}/messages`
    );
    return Promise.all(response.data.messages.map(mapMessage));
  };

  const loadSessionSnapshot = async (sessionId: string) => {
    const [sessionResponse, messages] = await Promise.all([
      apiClient.get<SessionResponse>(`/chat/sessions/${sessionId}`),
      loadSessionMessages(sessionId),
    ]);

    const nextSession = mapSession(sessionResponse.data, messages);
    setSessions(current => appendOrReplaceSession(current ?? [], nextSession));
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
        const response =
          await apiClient.get<SessionListResponse>("/chat/sessions");
        const loadedSessions = await Promise.all(
          response.data.sessions.map(async session => {
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
    chatSessionId?: string,
    storyContext?: StoryChatContext | null
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
    if (storyContext) {
      formData.set("story_context_json", JSON.stringify(storyContext));
    }
    attachments.forEach(attachment => {
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
    chatSessionId?: string,
    storyContext?: StoryChatContext | null
  ) => {
    const trimmedQuestion = question.trim();
    if (!trimmedQuestion && attachments.length === 0) {
      return null;
    }

    const token = getStoredAuthToken();
    if (!token) {
      throw new Error("Missing auth token.");
    }
    const controller = new AbortController();
    const streamState: ActiveStreamState = {
      controller,
      assistantMessageId: null,
      sessionId: chatSessionId ?? null,
      interrupted: false,
    };
    activeStreamRef.current = streamState;

    const formData = new FormData();
    if (chatSessionId) {
      formData.set("chat_session_id", chatSessionId);
    }
    if (trimmedQuestion) {
      formData.set("content_text", trimmedQuestion);
    }
    if (storyContext) {
      formData.set("story_context_json", JSON.stringify(storyContext));
    }
    attachments.forEach(attachment => {
      formData.append("images", attachment.file);
    });

    const response = await fetch(buildChatStreamEndpoint(), {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
      },
      body: formData,
      signal: controller.signal,
    });
    if (!response.ok || !response.body) {
      finishActiveStream(streamState);
      const body = await response.text();
      throw new Error(buildStreamErrorMessage(response.status, body));
    }

    return new Promise<string | null>((resolve, reject) => {
      let streamSessionId = chatSessionId ?? null;
      let streamAssistantMessageId: string | null = null;
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
              const payload =
                sseEvent.data as unknown as StreamMessageStartResponse;
              const userMessage = await mapMessage(payload.user_message);
              const assistantMessage = await mapMessage(
                payload.assistant_message
              );
              assistantMessage.responseJson = assistantMessage.responseJson ?? {
                message_type: "chat",
                phase: "retrieving",
              };
              const nextSessionId = payload.chat_session_id;
              streamSessionId = nextSessionId;
              streamAssistantMessageId = assistantMessage.id;
              streamState.sessionId = nextSessionId;
              streamState.assistantMessageId = assistantMessage.id;
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

              setSessions(current => {
                if (!current) {
                  return [nextSession];
                }

                const existingSession = current.find(
                  session => session.id === nextSessionId
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
              const delta =
                typeof sseEvent.data.delta === "string"
                  ? sseEvent.data.delta
                  : "";
              if (!streamSessionId || !streamAssistantMessageId || !delta) {
                continue;
              }
              const targetSessionId = streamSessionId;
              const targetAssistantMessageId = streamAssistantMessageId;

              setSessions(current => {
                if (!current) {
                  return current;
                }

                return appendAssistantDeltaToSessions(
                  current,
                  targetSessionId,
                  targetAssistantMessageId,
                  delta
                );
              });
              continue;
            }

            if (
              sseEvent.event === "message_complete" ||
              sseEvent.event === "message_interrupted" ||
              sseEvent.event === "message_error"
            ) {
              const targetSessionId = streamSessionId;
              const targetAssistantMessageId = streamAssistantMessageId;

              if (
                typeof sseEvent.data.chat_message_id === "string" &&
                targetSessionId
              ) {
                const nextMessage = await mapMessage(
                  sseEvent.data as unknown as MessageResponse
                );
                replaceMessage(targetSessionId, nextMessage);
              } else if (
                sseEvent.event === "message_error" &&
                targetSessionId &&
                targetAssistantMessageId
              ) {
                const detail =
                  typeof sseEvent.data.detail === "string"
                    ? sseEvent.data.detail
                    : "AI 回答失败，请稍后重试。";
                markMessageTerminalError(
                  targetSessionId,
                  targetAssistantMessageId,
                  detail
                );
              }

              if (sseEvent.event === "message_error") {
                toast.error("AI 回答失败，请查看会话中的错误信息。");
              }

              finishActiveStream(streamState);
            }
          }

          finishActiveStream(streamState);
          resolveOnce(chatSessionId ?? null);
        } catch (streamError) {
          finishActiveStream(streamState);
          if (streamState.interrupted && isAbortError(streamError)) {
            resolveOnce(streamSessionId ?? chatSessionId ?? null);
            return;
          }
          rejectOnce(streamError);
        }
      })();
    });
  };

  const createSession = async (
    question: string,
    attachments: ChatUploadAttachment[] = [],
    storyContext?: StoryChatContext | null
  ) => {
    try {
      return await streamMessage(question, attachments, undefined, storyContext);
    } catch (createError) {
      toast.error(extractErrorMessage(createError, "创建会话失败。"));
      return null;
    }
  };

  const createStorySession = async (
    question: string,
    attachments: ChatUploadAttachment[] = [],
    storyContext?: StoryChatContext | null
  ) => {
    return createSession(question, attachments, storyContext ?? null);
  };

  const createStoryDeepResearchSession = async (
    question: string,
    attachments: ChatUploadAttachment[] = [],
    storyContext?: StoryChatContext | null
  ) => {
    return createDeepResearchSession(question, attachments, storyContext ?? null);
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

  const streamDeepResearchMessage = async (
    question: string,
    attachments: ChatUploadAttachment[],
    chatSessionId?: string,
    storyContext?: StoryChatContext | null
  ) => {
    const trimmedQuestion = question.trim();
    if (!trimmedQuestion && attachments.length === 0) {
      return null;
    }

    const token = getStoredAuthToken();
    if (!token) {
      throw new Error("Missing auth token.");
    }
    const controller = new AbortController();
    const streamState: ActiveStreamState = {
      controller,
      assistantMessageId: null,
      sessionId: chatSessionId ?? null,
      interrupted: false,
    };
    activeStreamRef.current = streamState;

    const existingSession = chatSessionId
      ? ((sessions ?? []).find(item => item.id === chatSessionId) ?? null)
      : null;
    const threadId =
      findPendingDeepResearchInterrupt(existingSession)?.threadId;

    const formData = new FormData();
    if (chatSessionId) {
      formData.set("chat_session_id", chatSessionId);
    }
    if (trimmedQuestion) {
      formData.set("content_text", trimmedQuestion);
    }
    if (storyContext) {
      formData.set("story_context_json", JSON.stringify(storyContext));
    }
    if (threadId) {
      formData.set("thread_id", threadId);
    }
    attachments.forEach(attachment => {
      formData.append("images", attachment.file);
    });

    const response = await fetch(buildDeepResearchStreamEndpoint(), {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
      },
      body: formData,
      signal: controller.signal,
    });
    if (!response.ok || !response.body) {
      finishActiveStream(streamState);
      const body = await response.text();
      throw new Error(buildStreamErrorMessage(response.status, body));
    }

    return new Promise<string | null>((resolve, reject) => {
      let streamSessionId = chatSessionId ?? null;
      let streamAssistantMessageId: string | null = null;
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
              const payload =
                sseEvent.data as unknown as StreamMessageStartResponse;
              const userMessage = await mapMessage(payload.user_message);
              const assistantMessage = await mapMessage(
                payload.assistant_message
              );
              const nextSessionId = payload.chat_session_id;
              streamSessionId = nextSessionId;
              streamAssistantMessageId = assistantMessage.id;
              streamState.sessionId = nextSessionId;
              streamState.assistantMessageId = assistantMessage.id;
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

              setSessions(current => {
                if (!current) {
                  return [nextSession];
                }

                const currentSession = current.find(
                  session => session.id === nextSessionId
                );
                if (!currentSession) {
                  return appendOrReplaceSession(current, nextSession);
                }

                const nextMessages = currentSession.messages.concat([
                  userMessage,
                  assistantMessage,
                ]);
                return appendOrReplaceSession(current, {
                  ...currentSession,
                  title: payload.session_title,
                  updatedAt: payload.session_updated_at,
                  description: buildSessionDescription(nextMessages),
                  messages: nextMessages,
                });
              });
              resolveOnce(nextSessionId);
              continue;
            }

            if (sseEvent.event === "progress") {
              const targetSessionId = streamSessionId;
              const targetAssistantMessageId = streamAssistantMessageId;
              const node =
                typeof sseEvent.data.node === "string" ? sseEvent.data.node : null;

              if (targetSessionId && targetAssistantMessageId && node) {
                patchMessageResponseJson(targetSessionId, targetAssistantMessageId, {
                  message_type: "deep_research",
                  phase: "running",
                  current_node: node,
                });
              }
              continue;
            }

            if (
              sseEvent.event === "message_complete" ||
              sseEvent.event === "message_interrupted" ||
              sseEvent.event === "message_error"
            ) {
              const targetSessionId = streamSessionId;
              const targetAssistantMessageId = streamAssistantMessageId;

              if (
                typeof sseEvent.data.chat_message_id === "string" &&
                targetSessionId
              ) {
                const nextMessage = await mapMessage(
                  sseEvent.data as unknown as MessageResponse
                );
                replaceMessage(targetSessionId, nextMessage);
              } else if (
                sseEvent.event === "message_error" &&
                targetSessionId &&
                targetAssistantMessageId
              ) {
                const detail =
                  typeof sseEvent.data.detail === "string"
                    ? sseEvent.data.detail
                    : "深度研究失败，请稍后重试。";
                markMessageTerminalError(
                  targetSessionId,
                  targetAssistantMessageId,
                  detail
                );
              }

              if (sseEvent.event === "message_error") {
                toast.error("深度研究失败，请查看会话中的错误信息。");
              }

              finishActiveStream(streamState);
            }
          }

          finishActiveStream(streamState);
          resolveOnce(chatSessionId ?? null);
        } catch (streamError) {
          finishActiveStream(streamState);
          if (streamState.interrupted && isAbortError(streamError)) {
            resolveOnce(streamSessionId ?? chatSessionId ?? null);
            return;
          }
          rejectOnce(streamError);
        }
      })();
    });
  };

  const createDeepResearchSession = async (
    question: string,
    attachments: ChatUploadAttachment[] = [],
    storyContext?: StoryChatContext | null
  ) => {
    try {
      return await streamDeepResearchMessage(question, attachments, undefined, storyContext);
    } catch (createError) {
      toast.error(extractErrorMessage(createError, "创建深度研究失败。"));
      return null;
    }
  };

  const sendDeepResearchMessage = async (
    sessionId: string,
    question: string,
    attachments: ChatUploadAttachment[] = []
  ) => {
    try {
      await streamDeepResearchMessage(question, attachments, sessionId);
    } catch (sendError) {
      toast.error(extractErrorMessage(sendError, "发送深度研究请求失败。"));
    }
  };

  return {
    hydrated: !isAuthenticated || sessions !== null,
    error,
    sessions: orderedSessions,
    createSession,
    createDeepResearchSession,
    createStorySession,
    createStoryDeepResearchSession,
    sendMessage,
    sendDeepResearchMessage,
    canInterruptMessage,
    interruptMessage,
  };
}
