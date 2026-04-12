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

export interface ChatAssistantImageResult {
  id: string;
  title: string;
  imageUrl: string;
  previewUrl: string;
  sourceName: string;
  href: string;
  snippet: string;
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
  imageResults: ChatAssistantImageResult[];
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

function normalizeNonEmptyString(value: unknown) {
  if (typeof value !== "string") {
    return null;
  }

  const normalized = value.trim();
  return normalized || null;
}

function normalizeRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function normalizeRecordArray(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value.flatMap(item => {
    const record = normalizeRecord(item);
    return record ? [record] : [];
  });
}

function buildSourceLabel(url: string | null, fallback: string) {
  if (url) {
    try {
      return new URL(url).hostname || fallback;
    } catch {
      return fallback;
    }
  }

  return fallback;
}

function extractPackageImageResults(
  responseJson: Record<string, unknown>
): ChatAssistantImageResult[] {
  const packages = normalizeRecordArray(responseJson.packages);

  return packages.flatMap((pkg, packageIndex) => {
    const packageTitle = normalizeNonEmptyString(pkg.title);
    const packageSummary = normalizeNonEmptyString(pkg.summary) ?? "";

    return normalizeRecordArray(pkg.image_hits).flatMap((hit, hitIndex) => {
      const imageUrl = normalizeNonEmptyString(hit.source_url);
      if (!imageUrl) {
        return [];
      }

      const citationLocator = normalizeRecord(hit.citation_locator);
      const href =
        normalizeNonEmptyString(citationLocator?.canonical_url) ?? imageUrl;
      const sourceName =
        normalizeNonEmptyString(citationLocator?.source_name) ??
        buildSourceLabel(href, "内部资料");
      const title =
        normalizeNonEmptyString(hit.caption_raw) ??
        normalizeNonEmptyString(hit.alt_text) ??
        packageTitle ??
        "内部参考图";
      const snippet =
        normalizeNonEmptyString(hit.context_snippet) ?? packageSummary;
      const retrievalUnitId =
        normalizeNonEmptyString(hit.retrieval_unit_id) ??
        `package-${packageIndex}-image-${hitIndex}`;

      return [
        {
          id: `rag-${retrievalUnitId}`,
          imageUrl,
          previewUrl: imageUrl,
          href,
          title,
          sourceName,
          snippet,
        },
      ];
    });
  });
}

function extractExternalImageResults(
  responseJson: Record<string, unknown>
): ChatAssistantImageResult[] {
  return normalizeRecordArray(responseJson.external_visual_results).flatMap(
    (result, index) => {
      const imageUrl =
        normalizeNonEmptyString(result.image_url) ??
        normalizeNonEmptyString(result.thumbnail_url) ??
        normalizeNonEmptyString(result.url);
      if (!imageUrl) {
        return [];
      }

      const href =
        normalizeNonEmptyString(result.source_page_url) ??
        normalizeNonEmptyString(result.url) ??
        imageUrl;
      const sourceName =
        normalizeNonEmptyString(result.source_name) ??
        buildSourceLabel(href, "站外来源");
      const title = normalizeNonEmptyString(result.title) ?? "站外灵感图";
      const snippet =
        normalizeNonEmptyString(result.snippet) ??
        normalizeNonEmptyString(result.content) ??
        "";

      return [
        {
          id: `web-${index}-${href}`,
          imageUrl,
          previewUrl: imageUrl,
          href,
          title,
          sourceName,
          snippet,
        },
      ];
    }
  );
}

export function extractChatImageResults(
  responseJson: Record<string, unknown> | null
): ChatAssistantImageResult[] {
  if (!responseJson) {
    return [];
  }

  const deduped = new Map<string, ChatAssistantImageResult>();
  for (const result of [
    ...extractPackageImageResults(responseJson),
    ...extractExternalImageResults(responseJson),
  ]) {
    const key = `${result.imageUrl}::${result.href}`;
    if (!deduped.has(key)) {
      deduped.set(key, result);
    }
  }

  return Array.from(deduped.values());
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

function normalizeText(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

function normalizeObjectArray(value: unknown) {
  if (!Array.isArray(value)) {
    return [];
  }

  return value.filter(
    (item): item is Record<string, unknown> =>
      Boolean(item) && typeof item === "object"
  );
}

function buildAssistantImageSnippet(
  ...values: Array<string | undefined>
) {
  for (const value of values) {
    if (value && value.trim()) {
      return value.trim();
    }
  }

  return "查看图片参考";
}

function cleanAssistantImageSnippet(
  snippet: string,
  {
    title,
    sourceName,
  }: {
    title: string;
    sourceName: string;
  }
) {
  const normalizedSnippet = normalizeText(snippet);
  if (!normalizedSnippet) {
    return buildAssistantImageSnippet();
  }

  const lowerSnippet = normalizedSnippet.toLowerCase();
  const lowerTitle = normalizeText(title).toLowerCase();
  const lowerSourceName = normalizeText(sourceName).toLowerCase();

  if (
    (lowerTitle && lowerSnippet.startsWith(lowerTitle)) ||
    (lowerSourceName && lowerSnippet.startsWith(lowerSourceName)) ||
    normalizedSnippet.length > 140
  ) {
    return sourceName ? `点击查看 ${sourceName} 原始报道` : "点击查看原始报道";
  }

  return normalizedSnippet;
}

function mapExplicitAssistantImageResults(
  responseJson: Record<string, unknown> | null
) {
  const rawResults = normalizeObjectArray(responseJson?.image_results);

  return rawResults.flatMap((result, index) => {
    const imageUrl = normalizeText(result.image_url);
    if (!imageUrl) {
      return [];
    }

    const previewUrl =
      normalizeText(result.preview_url) ||
      imageUrl;
    const title = normalizeText(result.title) || `图片参考 ${index + 1}`;
    const sourceName = normalizeText(result.source_name) || "图片参考";
    const href =
      normalizeText(result.source_page_url) ||
      imageUrl;
    const snippet = cleanAssistantImageSnippet(
      buildAssistantImageSnippet(
        normalizeText(result.snippet),
        title
      ),
      {
        title,
        sourceName,
      }
    );

    return [
      {
        id: normalizeText(result.id) || `image-result-${index}-${href}`,
        title,
        imageUrl,
        previewUrl,
        sourceName,
        href,
        snippet,
      },
    ];
  });
}

function dedupeAssistantImageResults(results: ChatAssistantImageResult[]) {
  const seen = new Set<string>();
  return results.filter(result => {
    const key = `${result.imageUrl}::${result.href}`;
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
}

function mapExternalVisualImageResults(
  responseJson: Record<string, unknown> | null
) {
  const rawResults = normalizeObjectArray(responseJson?.external_visual_results);

  return rawResults.flatMap((result, index) => {
    const imageUrl =
      normalizeText(result.image_url) ||
      normalizeText(result.thumbnail_url) ||
      normalizeText(result.url);
    if (!imageUrl) {
      return [];
    }

    const title = normalizeText(result.title) || `图片参考 ${index + 1}`;
    const sourceName = normalizeText(result.source_name) || "外部图片参考";
    const href = normalizeText(result.source_page_url) || normalizeText(result.url) || imageUrl;
    const snippet = cleanAssistantImageSnippet(
      buildAssistantImageSnippet(
        normalizeText(result.snippet),
        normalizeText(result.content),
        title
      ),
      {
        title,
        sourceName,
      }
    );

    return [
      {
        id: `external-${normalizeText(result.provider) || "visual"}-${index}-${imageUrl}`,
        title,
        imageUrl,
        previewUrl: normalizeText(result.thumbnail_url) || imageUrl,
        sourceName,
        href,
        snippet,
      },
    ];
  });
}

function mapPackageImageResults(
  responseJson: Record<string, unknown> | null
) {
  const rawPackages = normalizeObjectArray(responseJson?.packages);

  return rawPackages.flatMap((pkg, packageIndex) => {
    const packageTitle = normalizeText(pkg.title);
    const packageSummary = normalizeText(pkg.summary);
    const rawImageHits = normalizeObjectArray(pkg.image_hits);

    return rawImageHits.flatMap((hit, imageIndex) => {
      const imageUrl = normalizeText(hit.source_url);
      if (!imageUrl) {
        return [];
      }

      const citationLocator =
        hit.citation_locator && typeof hit.citation_locator === "object"
          ? (hit.citation_locator as Record<string, unknown>)
          : null;
      const title =
        normalizeText(hit.title) ||
        packageTitle ||
        `内部图片参考 ${packageIndex + 1}-${imageIndex + 1}`;
      const sourceName =
        normalizeText(citationLocator?.source_name) || "内部图片参考";
      const href =
        normalizeText(citationLocator?.canonical_url) || imageUrl;
      const snippet = cleanAssistantImageSnippet(
        buildAssistantImageSnippet(
          normalizeText(hit.caption_raw),
          normalizeText(hit.alt_text),
          normalizeText(hit.context_snippet),
          packageSummary,
          title
        ),
        {
          title,
          sourceName,
        }
      );

      return [
        {
          id: `rag-${packageIndex}-${imageIndex}-${imageUrl}`,
          title,
          imageUrl,
          previewUrl: imageUrl,
          sourceName,
          href,
          snippet,
        },
      ];
    });
  });
}

export function mapAssistantImageResults(
  responseJson: Record<string, unknown> | null
) {
  const explicitResults = dedupeAssistantImageResults(
    mapExplicitAssistantImageResults(responseJson)
  );
  if (explicitResults.length > 0) {
    return explicitResults;
  }

  const externalResults = mapExternalVisualImageResults(responseJson);
  if (externalResults.length > 0) {
    return dedupeAssistantImageResults(externalResults);
  }

  return dedupeAssistantImageResults(mapPackageImageResults(responseJson));
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
