import { describe, expect, it, vi } from "vitest";
import { resolveRenderableAttachmentUrl } from "./chat-attachments";

describe("resolveRenderableAttachmentUrl", () => {
  it("loads protected chat attachment URLs through the authenticated loader", async () => {
    const loadProtectedAttachmentUrl = vi.fn(async () => "blob:chat-image");

    const nextUrl = await resolveRenderableAttachmentUrl(
      "/api/v1/chat/attachments/attachment-1/content",
      {
        token: "token-1",
        loadProtectedAttachmentUrl,
      }
    );

    expect(loadProtectedAttachmentUrl).toHaveBeenCalledWith(
      "/api/v1/chat/attachments/attachment-1/content",
      "token-1"
    );
    expect(nextUrl).toBe("blob:chat-image");
  });

  it("leaves public attachment URLs unchanged", async () => {
    const loadProtectedAttachmentUrl = vi.fn(async () => "blob:chat-image");

    const nextUrl = await resolveRenderableAttachmentUrl(
      "https://cdn.example.com/image.png",
      {
        token: "token-1",
        loadProtectedAttachmentUrl,
      }
    );

    expect(loadProtectedAttachmentUrl).not.toHaveBeenCalled();
    expect(nextUrl).toBe("https://cdn.example.com/image.png");
  });
});
