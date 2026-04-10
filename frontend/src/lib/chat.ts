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

export interface StoryChatContext {
  title: string;
  summary: string;
  keyPoints: string[];
  bodyMarkdown?: string;
  sourceNames: string[];
}

export interface ChatMessage {
  id: string;
  role: "assistant" | "user";
  content: string;
  createdAt: string;
  status: "done" | "queued" | "running" | "interrupted" | "failed";
  errorMessage: string | null;
  responseJson: Record<string, unknown> | null;
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

export interface PendingDeepResearchInterrupt {
  threadId: string;
  messageId: string;
}

const CITATION_MARKER_RE = /\[([a-z]\d+)\]/gi;

const DEFAULT_SESSION_DESCRIPTION = "查看对话详情";
const DEEP_RESEARCH_PROGRESS_LABELS: Record<string, string> = {
  clarify: "正在澄清研究问题...",
  planner: "正在规划研究路径...",
  outline_reviser: "正在整理研究提纲...",
  section_pipeline: "正在拆解章节任务...",
  section_worker: "正在撰写章节内容...",
  lead_writer: "正在整合主体内容...",
  synthesizer: "正在汇总结论...",
  trend_triangulator: "正在交叉验证趋势...",
  reviewer: "正在复核内容...",
  reviser: "正在修订表达...",
  final_check: "正在生成最终答案...",
};

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
    .find(message => message.role === "assistant" && message.content.trim());
  if (latestAssistant) {
    return shortenLine(
      latestAssistant.content.split("\n")[0] ?? latestAssistant.content
    );
  }

  const latestUser = [...messages]
    .reverse()
    .find(message => message.role === "user" && message.content.trim());
  if (latestUser) {
    return shortenLine(latestUser.content.split("\n")[0] ?? latestUser.content);
  }

  return DEFAULT_SESSION_DESCRIPTION;
}

export function sortChatSessions(sessions: ChatSession[]) {
  return [...sessions].sort((left, right) =>
    right.updatedAt.localeCompare(left.updatedAt)
  );
}

export function findPendingDeepResearchInterrupt(
  session: ChatSession | null | undefined
): PendingDeepResearchInterrupt | null {
  const lastMessage = session?.messages[session.messages.length - 1];
  if (
    !lastMessage ||
    lastMessage.role !== "assistant" ||
    lastMessage.status !== "done" ||
    !lastMessage.responseJson
  ) {
    return null;
  }

  const messageType = lastMessage.responseJson["message_type"];
  const phase = lastMessage.responseJson["phase"];
  const threadId = lastMessage.responseJson["thread_id"];

  if (
    messageType !== "deep_research" ||
    phase !== "clarification" ||
    typeof threadId !== "string" ||
    !threadId.trim()
  ) {
    return null;
  }

  return {
    threadId: threadId.trim(),
    messageId: lastMessage.id,
  };
}

export function buildAssistantPendingLabel(message: ChatMessage) {
  if (message.role !== "assistant" || message.status !== "running") {
    return null;
  }

  const payload = message.responseJson;
  if (!payload) {
    return "正在生成回答...";
  }

  const messageType = payload["message_type"];
  if (messageType === "deep_research") {
    const currentNode = payload["current_node"];
    if (
      typeof currentNode === "string" &&
      DEEP_RESEARCH_PROGRESS_LABELS[currentNode]
    ) {
      return DEEP_RESEARCH_PROGRESS_LABELS[currentNode];
    }

    const phase = payload["phase"];
    if (phase === "clarification") {
      return "等待你补充研究方向...";
    }

    return "正在进行深度研究...";
  }

  if (payload["phase"] === "retrieving") {
    return "正在检索资料...";
  }

  return "正在生成回答...";
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
  const rows = blockLines.map(line =>
    line.split("\t").map(cell => cell.trim())
  );
  const columnCount = rows[0]?.length ?? 0;
  if (blockLines.length < 2 || columnCount < 2) {
    return blockLines;
  }
  if (rows.some(row => row.length !== columnCount)) {
    return blockLines;
  }

  return [
    `| ${rows[0].map(escapeMarkdownTableCell).join(" | ")} |`,
    `| ${rows[0].map(() => "---").join(" | ")} |`,
    ...rows
      .slice(1)
      .map(row => `| ${row.map(escapeMarkdownTableCell).join(" | ")} |`),
  ];
}

function normalizeAssistantMarkdown(content: string) {
  const normalizedLines = content
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .map(line => line.replace(/[ \t]+$/g, ""));

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

function buildCitationDisplayKey(citation: ChatCitation) {
  return citation.href.trim();
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
    citations.map(citation => [citation.marker.toLowerCase(), citation])
  );

  let result = "";
  let lastIndex = 0;
  let previousCitationKey: string | null = null;
  const citationMarkerPattern = new RegExp(CITATION_MARKER_RE);
  let match = citationMarkerPattern.exec(normalizedContent);
  const displayIndexByCitationKey = new Map<string, number>();
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

    const citationKey = buildCitationDisplayKey(citation);

    if (citationKey === previousCitationKey && plainText.trim() === "") {
      lastIndex = matchIndex + match[0].length;
      match = citationMarkerPattern.exec(normalizedContent);
      continue;
    }

    let displayIndex = displayIndexByCitationKey.get(citationKey);
    if (!displayIndex) {
      displayIndex = nextDisplayIndex;
      displayIndexByCitationKey.set(citationKey, displayIndex);
      nextDisplayIndex += 1;
    }

    result += plainText;
    result += `[${displayIndex}](<${citation.href}> "${escapeMarkdownLinkTitle(
      `${citation.sourceName} · ${citation.title}`
    )}")`;
    previousCitationKey = citationKey;
    lastIndex = matchIndex + match[0].length;
    match = citationMarkerPattern.exec(normalizedContent);
  }

  result += normalizedContent.slice(lastIndex);
  return result;
}
