export interface ChatCitation {
  id: string;
  marker: string;
  title: string;
  sourceName: string;
  href: string;
  snippet: string;
}

export interface ChatAttachment {
  id: string;
  name: string;
  mimeType: string;
  size: number;
  url: string;
}

export interface ChatUploadAttachment {
  id: string;
  file: File;
  previewUrl: string;
  name: string;
  mimeType: string;
  size: number;
}

export interface ChatMessage {
  id: string;
  role: "assistant" | "user";
  content: string;
  createdAt: string;
  status: "done" | "queued" | "running" | "failed";
  errorMessage: string | null;
  citations: ChatCitation[];
  attachments: ChatAttachment[];
}

export interface ChatSession {
  id: string;
  title: string;
  description: string;
  updatedAt: string;
  messages: ChatMessage[];
}

const CITATION_MARKER_RE = /\[([a-z]\d+)\]/gi;

const DEFAULT_SESSION_DESCRIPTION = "查看对话详情";

function shortenLine(value: string, maxLength = 72) {
  const normalized = value.trim().replace(/\s+/g, " ");
  if (!normalized) {
    return DEFAULT_SESSION_DESCRIPTION;
  }

  return normalized.length > maxLength
    ? `${normalized.slice(0, maxLength - 1)}…`
    : normalized;
}

export function buildSessionDescription(messages: ChatMessage[]) {
  const latestAssistant = [...messages]
    .reverse()
    .find((message) => message.role === "assistant" && message.content.trim());
  if (latestAssistant) {
    return shortenLine(latestAssistant.content.split("\n")[0] ?? latestAssistant.content);
  }

  const latestUser = [...messages]
    .reverse()
    .find((message) => message.role === "user" && message.content.trim());
  if (latestUser) {
    return shortenLine(latestUser.content.split("\n")[0] ?? latestUser.content);
  }

  return DEFAULT_SESSION_DESCRIPTION;
}

export function sortChatSessions(sessions: ChatSession[]) {
  return [...sessions].sort((left, right) => right.updatedAt.localeCompare(left.updatedAt));
}

function escapeMarkdownLinkTitle(value: string) {
  return value.replace(/"/g, "'").replace(/\s+/g, " ").trim();
}

function escapeMarkdownTableCell(value: string) {
  return value.replace(/\|/g, "\\|");
}

function collapseBlankLines(lines: string[]) {
  const collapsed: string[] = [];
  let previousBlank = true;

  for (const line of lines) {
    const isBlank = line.trim() === "";
    if (isBlank) {
      if (previousBlank) {
        continue;
      }
      collapsed.push("");
      previousBlank = true;
      continue;
    }

    collapsed.push(line);
    previousBlank = false;
  }

  if (collapsed[collapsed.length - 1] === "") {
    collapsed.pop();
  }
  return collapsed;
}

function convertTabDelimitedBlock(blockLines: string[]) {
  const rows = blockLines.map((line) =>
    line.split("\t").map((cell) => cell.trim())
  );
  const columnCount = rows[0]?.length ?? 0;
  if (blockLines.length < 2 || columnCount < 2) {
    return blockLines;
  }
  if (rows.some((row) => row.length !== columnCount)) {
    return blockLines;
  }

  return [
    `| ${rows[0].map(escapeMarkdownTableCell).join(" | ")} |`,
    `| ${rows[0].map(() => "---").join(" | ")} |`,
    ...rows.slice(1).map(
      (row) => `| ${row.map(escapeMarkdownTableCell).join(" | ")} |`
    ),
  ];
}

function normalizeAssistantMarkdown(content: string) {
  const normalizedLines = content
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .map((line) => line.replace(/[ \t]+$/g, ""));

  const convertedLines: string[] = [];
  for (let index = 0; index < normalizedLines.length; index += 1) {
    const line = normalizedLines[index] ?? "";
    if (!line.trim() || !line.includes("\t")) {
      convertedLines.push(line);
      continue;
    }

    const block = [line];
    let nextIndex = index + 1;
    while (
      nextIndex < normalizedLines.length &&
      (normalizedLines[nextIndex] ?? "").trim() &&
      (normalizedLines[nextIndex] ?? "").includes("\t")
    ) {
      block.push(normalizedLines[nextIndex] ?? "");
      nextIndex += 1;
    }

    convertedLines.push(...convertTabDelimitedBlock(block));
    index = nextIndex - 1;
  }

  return collapseBlankLines(convertedLines).join("\n").trim();
}

export function buildCitationAwareMarkdown(
  content: string,
  citations: ChatCitation[]
) {
  const normalizedContent = normalizeAssistantMarkdown(content);
  if (!normalizedContent || citations.length === 0) {
    return normalizedContent;
  }

  const citationByMarker = new Map(
    citations.map((citation) => [citation.marker.toLowerCase(), citation])
  );

  let result = "";
  let lastIndex = 0;
  let previousCitationId: string | null = null;
  const citationMarkerPattern = new RegExp(CITATION_MARKER_RE);
  let match = citationMarkerPattern.exec(normalizedContent);
  const displayIndexByCitationId = new Map<string, number>();
  let nextDisplayIndex = 1;

  while (match) {
    const matchIndex = match.index ?? 0;
    const plainText = normalizedContent.slice(lastIndex, matchIndex);
    const markerKey = match[1]?.toLowerCase() ?? "";
    const citation = citationByMarker.get(markerKey);

    if (!citation) {
      result += plainText;
      lastIndex = matchIndex + match[0].length;
      match = citationMarkerPattern.exec(normalizedContent);
      continue;
    }

    if (citation.id === previousCitationId && plainText.trim() === "") {
      lastIndex = matchIndex + match[0].length;
      match = citationMarkerPattern.exec(normalizedContent);
      continue;
    }

    let displayIndex = displayIndexByCitationId.get(citation.id);
    if (!displayIndex) {
      displayIndex = nextDisplayIndex;
      displayIndexByCitationId.set(citation.id, displayIndex);
      nextDisplayIndex += 1;
    }

    result += plainText;
    result += `[${displayIndex}](<${citation.href}> "${escapeMarkdownLinkTitle(
      `${citation.sourceName} · ${citation.title}`
    )}")`;
    previousCitationId = citation.id;
    lastIndex = matchIndex + match[0].length;
    match = citationMarkerPattern.exec(normalizedContent);
  }

  result += normalizedContent.slice(lastIndex);
  return result;
}
