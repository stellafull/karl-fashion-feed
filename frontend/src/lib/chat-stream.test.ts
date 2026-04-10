import { describe, expect, it } from "vitest";
import type { ChatSession } from "./chat";
import {
  appendAssistantDeltaToSessions,
  buildChatViewportSpacing,
  buildLastMessageScrollKey,
  findLatestRunningAssistantMessageId,
  interruptAssistantMessageInSessions,
} from "./chat-stream";

function createSession(): ChatSession {
  return {
    id: "session-1",
    title: "Session",
    description: "desc",
    updatedAt: "2026-03-24T09:00:00.000Z",
    messages: [
      {
        id: "user-1",
        role: "user",
        content: "Earlier question",
        createdAt: "2026-03-24T09:00:00.000Z",
        status: "done",
        errorMessage: null,
        responseJson: null,
        citations: [],
        attachments: [],
      },
      {
        id: "assistant-old",
        role: "assistant",
        content: "Earlier answer",
        createdAt: "2026-03-24T09:00:01.000Z",
        status: "done",
        errorMessage: null,
        responseJson: null,
        citations: [],
        attachments: [],
      },
      {
        id: "user-2",
        role: "user",
        content: "Newest question",
        createdAt: "2026-03-24T09:00:02.000Z",
        status: "done",
        errorMessage: null,
        responseJson: null,
        citations: [],
        attachments: [],
      },
      {
        id: "assistant-new",
        role: "assistant",
        content: "",
        createdAt: "2026-03-24T09:00:03.000Z",
        status: "running",
        errorMessage: null,
        responseJson: {
          message_type: "chat",
          phase: "retrieving",
        },
        citations: [],
        attachments: [],
      },
    ],
  };
}

describe("appendAssistantDeltaToSessions", () => {
  it("appends delta to the current streaming assistant only", () => {
    const sessions = [createSession()];

    const nextSessions = appendAssistantDeltaToSessions(
      sessions,
      "session-1",
      "assistant-new",
      "Hello"
    );

    expect(nextSessions[0]?.messages[1]?.content).toBe("Earlier answer");
    expect(nextSessions[0]?.messages[3]?.content).toBe("Hello");
    expect(nextSessions[0]?.messages[3]?.status).toBe("running");
    expect(nextSessions[0]?.messages[3]?.responseJson).toEqual({
      message_type: "chat",
      phase: "answering",
    });
  });
});

describe("buildLastMessageScrollKey", () => {
  it("changes when the last streaming message grows", () => {
    const session = createSession();

    const before = buildLastMessageScrollKey(session.messages);
    const nextSessions = appendAssistantDeltaToSessions(
      [session],
      "session-1",
      "assistant-new",
      "Hello"
    );
    const after = buildLastMessageScrollKey(nextSessions[0]!.messages);

    expect(after).not.toBe(before);
  });
});

describe("interruptAssistantMessageInSessions", () => {
  it("marks the active assistant as interrupted", () => {
    const nextSessions = interruptAssistantMessageInSessions(
      [createSession()],
      "session-1",
      "assistant-new"
    );

    expect(nextSessions[0]?.messages[1]?.status).toBe("done");
    expect(nextSessions[0]?.messages[3]?.status).toBe("interrupted");
    expect(nextSessions[0]?.messages[3]?.errorMessage).toBe("回答已中断。");
  });
});

describe("findLatestRunningAssistantMessageId", () => {
  it("returns the latest running assistant id", () => {
    expect(findLatestRunningAssistantMessageId(createSession().messages)).toBe(
      "assistant-new"
    );
  });
});

describe("buildChatViewportSpacing", () => {
  it("keeps extra clearance above the composer and grows with composer height", () => {
    const compact = buildChatViewportSpacing(120);
    const tall = buildChatViewportSpacing(260);

    expect(compact.bottomPadding).toBeGreaterThan(120);
    expect(compact.scrollMarginBottom).toBe(compact.bottomPadding);
    expect(tall.bottomPadding).toBeGreaterThan(compact.bottomPadding);
  });
});
