const CHINA_TIME_ZONE = "Asia/Shanghai";
const OFFSET_SUFFIX_RE = /(Z|[+-]\d{2}:\d{2})$/i;

function parseFeedDate(dateStr: string): Date | null {
  if (!dateStr) return null;

  const raw = dateStr.trim();
  if (!raw) return null;

  // Historical feed payloads store ISO strings without timezone info.
  // Treat those values as UTC so China-facing UI does not drift by 8 hours.
  const normalized = OFFSET_SUFFIX_RE.test(raw) ? raw : `${raw}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatChinaDateTime(
  dateStr: string,
  options: Intl.DateTimeFormatOptions,
): string {
  const date = parseFeedDate(dateStr);
  if (!date) return "";

  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: CHINA_TIME_ZONE,
    hour12: false,
    ...options,
  }).format(date);
}

export function formatChinaDateTimeShort(dateStr: string): string {
  return formatChinaDateTime(dateStr, {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatChinaDateTimeFull(dateStr: string): string {
  return formatChinaDateTime(dateStr, {
    year: "numeric",
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
