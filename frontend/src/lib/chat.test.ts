import { describe, expect, it } from "vitest";
import {
  buildAssistantPendingLabel,
  buildCitationAwareMarkdown,
  extractChatImageResults,
  findPendingDeepResearchInterrupt,
  type ChatAssistantImageResult,
  type ChatMessage,
  type ChatSession,
} from "./chat";

function createMessage(overrides: Partial<ChatMessage>): ChatMessage {
  return {
    id: overrides.id ?? "message-1",
    role: overrides.role ?? "assistant",
    content: overrides.content ?? "",
    createdAt: overrides.createdAt ?? "2026-04-09T03:00:00.000Z",
    status: overrides.status ?? "done",
    errorMessage: overrides.errorMessage ?? null,
    responseJson: overrides.responseJson ?? null,
    citations: overrides.citations ?? [],
    imageResults: overrides.imageResults ?? [],
    attachments: overrides.attachments ?? [],
  };
}

function createSession(messages: ChatMessage[]): ChatSession {
  return {
    id: "session-1",
    title: "Deep Research · Session",
    description: "desc",
    updatedAt: "2026-04-09T03:00:00.000Z",
    messages,
  };
}

describe("findPendingDeepResearchInterrupt", () => {
  it("returns the thread id when the latest message is a clarification prompt", () => {
    const session = createSession([
      createMessage({
        id: "assistant-clarify",
        responseJson: {
          message_type: "deep_research",
          thread_id: "thread-clarify",
          phase: "clarification",
        },
      }),
    ]);

    expect(findPendingDeepResearchInterrupt(session)).toEqual({
      threadId: "thread-clarify",
      messageId: "assistant-clarify",
    });
  });

  it("ignores stale clarification messages once newer turns exist", () => {
    const session = createSession([
      createMessage({
        id: "assistant-clarify",
        responseJson: {
          message_type: "deep_research",
          thread_id: "thread-clarify",
          phase: "clarification",
        },
      }),
      createMessage({
        id: "user-follow-up",
        role: "user",
        content: "还有 Prada",
        responseJson: null,
      }),
    ]);

    expect(findPendingDeepResearchInterrupt(session)).toBeNull();
  });
});

describe("buildAssistantPendingLabel", () => {
  it("shows retrieving status for chat before the first delta arrives", () => {
    expect(
      buildAssistantPendingLabel(
        createMessage({
          status: "running",
          responseJson: {
            message_type: "chat",
            phase: "retrieving",
          },
        })
      )
    ).toBe("正在检索资料...");
  });

  it("maps deep research progress nodes to visible status text", () => {
    expect(
      buildAssistantPendingLabel(
        createMessage({
          status: "running",
          responseJson: {
            message_type: "deep_research",
            phase: "running",
            current_node: "planner",
          },
        })
      )
    ).toBe("正在规划研究路径...");
  });
});

describe("buildCitationAwareMarkdown", () => {
  it("reuses the same display index for repeated citations with the same url", () => {
    const markdown = buildCitationAwareMarkdown("结论[a1][a2] 延伸[a3]", [
      {
        id: "citation-1",
        marker: "a1",
        title: "来源一",
        sourceName: "Vogue",
        href: "https://example.com/story",
        snippet: "",
      },
      {
        id: "citation-2",
        marker: "a2",
        title: "来源一-重复块",
        sourceName: "Vogue",
        href: "https://example.com/story",
        snippet: "",
      },
      {
        id: "citation-3",
        marker: "a3",
        title: "另一来源",
        sourceName: "WWD",
        href: "https://example.com/other",
        snippet: "",
      },
    ]);

    expect(markdown).toContain("[1](<https://example.com/story>");
    expect(markdown.match(/\[1\]\(<https:\/\/example\.com\/story>/g)?.length).toBe(1);
    expect(markdown).toContain("[2](<https://example.com/other>");
    expect(markdown).not.toContain("[3](<https://example.com/story>");
  });
});

describe("extractChatImageResults", () => {
  it("collects internal image hits before external visual results", () => {
    const imageResults = extractChatImageResults({
      packages: [
        {
          title: "Runway look",
          summary: "A strong accessories direction",
          image_hits: [
            {
              retrieval_unit_id: "image:image-1",
              source_url: "https://images.example.com/internal-look.jpg",
              caption_raw: "Green acetate sunglasses",
              context_snippet: "Rectangular frames from the runway look.",
              citation_locator: {
                canonical_url: "https://example.com/internal-story",
                source_name: "Vogue",
              },
            },
          ],
        },
      ],
      external_visual_results: [
        {
          title: "Street style match",
          image_url: "https://images.example.com/external-look.jpg",
          source_page_url: "https://news.example.com/external-story",
          source_name: "news.example.com",
          snippet: "Related street style inspiration",
        },
      ],
    });

    expect(imageResults).toEqual<ChatAssistantImageResult[]>([
      {
        id: "rag-image:image-1",
        imageUrl: "https://images.example.com/internal-look.jpg",
        previewUrl: "https://images.example.com/internal-look.jpg",
        href: "https://example.com/internal-story",
        title: "Green acetate sunglasses",
        sourceName: "Vogue",
        snippet: "Rectangular frames from the runway look.",
      },
      {
        id: "web-0-https://news.example.com/external-story",
        imageUrl: "https://images.example.com/external-look.jpg",
        previewUrl: "https://images.example.com/external-look.jpg",
        href: "https://news.example.com/external-story",
        title: "Street style match",
        sourceName: "news.example.com",
        snippet: "Related street style inspiration",
      },
    ]);
  });

  it("skips entries without a renderable image url", () => {
    expect(
      extractChatImageResults({
        packages: [
          {
            image_hits: [
              {
                retrieval_unit_id: "image:image-1",
                caption_raw: "Missing source url",
              },
            ],
          },
        ],
      })
    ).toEqual([]);
  });
});
