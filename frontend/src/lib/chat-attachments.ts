import type { ChatAttachment } from "./chat";
import { getStoredAuthToken, resolveApiUrl } from "./api-client";

interface AttachmentResponse {
  chat_attachment_id: string;
  mime_type: string;
  original_filename: string;
  size_bytes: number;
  content_url: string;
}

const PROTECTED_CHAT_ATTACHMENT_PATH_RE =
  /\/api\/v1\/chat\/attachments\/[^/]+\/content(?:$|\?)/i;

const protectedAttachmentUrlCache = new Map<string, Promise<string>>();

export function isProtectedChatAttachmentUrl(url: string) {
  return PROTECTED_CHAT_ATTACHMENT_PATH_RE.test(url);
}

async function loadProtectedAttachmentUrl(url: string, token: string) {
  const response = await fetch(resolveApiUrl(url), {
    headers: {
      Authorization: `Bearer ${token}`,
    },
  });
  if (!response.ok) {
    throw new Error(`Failed to load chat attachment: ${response.status}`);
  }

  const blob = await response.blob();
  return URL.createObjectURL(blob);
}

export async function resolveRenderableAttachmentUrl(
  url: string,
  {
    token = getStoredAuthToken(),
    loadProtectedAttachmentUrl: loadAttachment = loadProtectedAttachmentUrl,
  }: {
    token?: string | null;
    loadProtectedAttachmentUrl?: (url: string, token: string) => Promise<string>;
  } = {}
) {
  if (!isProtectedChatAttachmentUrl(url)) {
    return resolveApiUrl(url);
  }

  if (!token) {
    throw new Error("Missing auth token for protected chat attachment.");
  }

  const cachedUrl = protectedAttachmentUrlCache.get(url);
  if (cachedUrl) {
    return cachedUrl;
  }

  const nextUrlPromise = loadAttachment(url, token);
  protectedAttachmentUrlCache.set(url, nextUrlPromise);
  return nextUrlPromise;
}

export async function mapAttachmentResponse(
  attachment: AttachmentResponse
): Promise<ChatAttachment> {
  return {
    id: attachment.chat_attachment_id,
    name: attachment.original_filename,
    mimeType: attachment.mime_type,
    size: attachment.size_bytes,
    url: await resolveRenderableAttachmentUrl(attachment.content_url),
  };
}
