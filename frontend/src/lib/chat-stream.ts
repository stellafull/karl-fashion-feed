import { buildSessionDescription, type ChatMessage, type ChatSession } from "./chat";

export function appendAssistantDelta(
  messages: ChatMessage[],
  assistantMessageId: string,
  delta: string
) {
  if (!delta) {
    return messages;
  }

  let didUpdate = false;
  const nextMessages = messages.map((message) => {
    if (message.id !== assistantMessageId) {
      return message;
    }

    didUpdate = true;
    return {
      ...message,
      status: "running" as const,
      content: `${message.content}${delta}`,
    };
  });

  return didUpdate ? nextMessages : messages;
}

export function appendAssistantDeltaToSessions(
  sessions: ChatSession[],
  sessionId: string,
  assistantMessageId: string,
  delta: string
) {
  return sessions.map((session) => {
    if (session.id !== sessionId) {
      return session;
    }

    const nextMessages = appendAssistantDelta(
      session.messages,
      assistantMessageId,
      delta
    );
    if (nextMessages === session.messages) {
      return session;
    }

    return {
      ...session,
      description: buildSessionDescription(nextMessages),
      messages: nextMessages,
    };
  });
}

export function buildLastMessageScrollKey(messages: ChatMessage[]) {
  const lastMessage = messages[messages.length - 1];
  if (!lastMessage) {
    return "empty";
  }

  return [
    lastMessage.id,
    lastMessage.status,
    lastMessage.content.length,
    lastMessage.attachments.length,
  ].join(":");
}

const DEFAULT_COMPOSER_HEIGHT = 220;
const COMPOSER_CLEARANCE = 24;

export function buildChatViewportSpacing(composerHeight: number) {
  const effectiveComposerHeight =
    composerHeight > 0 ? composerHeight : DEFAULT_COMPOSER_HEIGHT;
  const bottomPadding = effectiveComposerHeight + COMPOSER_CLEARANCE;

  return {
    bottomPadding,
    scrollMarginBottom: bottomPadding,
  };
}
