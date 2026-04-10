import { useCallback, useEffect, useMemo, useState } from "react";
import apiClient from "@/lib/api-client";
import { formatChinaDateTimeShort } from "@/lib/time";

// ─── Data Types (v3: Topic-based, Luxury Brand Focus) ────────────────────

export interface TopicSource {
  name: string;
  title: string;
  link: string;
  lang: string;
}

export interface Topic {
  id: string;
  title: string;
  summary: string;
  key_points: string[];
  tags: string[];
  category: string;
  category_name: string;
  image: string;
  published: string;
  sources: TopicSource[];
  article_count: number;
}

export interface Category {
  id: string;
  name: string;
  icon: string;
}

export interface FeedMeta {
  generated_at: string;
  total_topics: number;
  total_articles: number;
  sources_count: number;
  sources: string[];
}

export interface FeedData {
  meta: FeedMeta;
  categories: Category[];
  topics: Topic[];
}

interface DigestFeedItem {
  id: string;
  facet: string;
  title: string;
  dek: string;
  image: string;
  published: string;
  article_count: number;
  source_count: number;
  source_names: string[];
}

interface DigestFeedResponse {
  digests: DigestFeedItem[];
}

const DIGEST_CATEGORY_CONFIG: Record<
  string,
  { name: string; icon: string }
> = {
  runway_series: { name: "秀场/系列", icon: "Sparkles" },
  street_style: { name: "街拍/造型", icon: "Camera" },
  trend_summary: { name: "趋势总结", icon: "TrendingUp" },
  brand_market: { name: "品牌/市场", icon: "Building2" },
};

function buildCategories(digests: DigestFeedItem[]): Category[] {
  const categories: Category[] = [{ id: "all", name: "全部", icon: "Newspaper" }];
  const seen = new Set<string>();

  for (const digest of digests) {
    if (seen.has(digest.facet)) {
      continue;
    }
    seen.add(digest.facet);
    const config = DIGEST_CATEGORY_CONFIG[digest.facet] ?? {
      name: digest.facet,
      icon: "Newspaper",
    };
    categories.push({
      id: digest.facet,
      name: config.name,
      icon: config.icon,
    });
  }

  return categories;
}

function mapDigestToTopic(digest: DigestFeedItem): Topic {
  const categoryConfig = DIGEST_CATEGORY_CONFIG[digest.facet] ?? {
    name: digest.facet,
    icon: "Newspaper",
  };
  const uniqueSourceNames = Array.from(new Set(digest.source_names));

  return {
    id: digest.id,
    title: digest.title,
    summary: digest.dek,
    key_points: [
      digest.dek,
      `综合 ${digest.article_count} 篇报道，覆盖 ${digest.source_count} 个来源。`,
    ],
    tags: [categoryConfig.name, ...uniqueSourceNames.slice(0, 3)],
    category: digest.facet,
    category_name: categoryConfig.name,
    image: digest.image,
    published: digest.published,
    article_count: digest.article_count,
    sources: uniqueSourceNames.map((sourceName) => ({
      name: sourceName,
      title: `${sourceName} 报道`,
      link: "#",
      lang: "",
    })),
  };
}

function mapDigestFeedToFeedData(payload: DigestFeedResponse): FeedData {
  const digests = payload.digests ?? [];
  const topics = digests.map(mapDigestToTopic);
  const sources = Array.from(
    new Set(digests.flatMap((digest) => digest.source_names ?? []))
  );

  return {
    meta: {
      generated_at: new Date().toISOString(),
      total_topics: topics.length,
      total_articles: topics.reduce((sum, topic) => sum + topic.article_count, 0),
      sources_count: sources.length,
      sources,
    },
    categories: buildCategories(digests),
    topics,
  };
}

export type SortMode = "newest" | "oldest" | "most-sources";

// ─── Hook ──────────────────────────────────────────────────────────────────

export function useFeedData() {
  const [data, setData] = useState<FeedData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeCategory, setActiveCategory] = useState("all");
  const [selectedTopic, setSelectedTopic] = useState<Topic | null>(null);
  const [sortMode, setSortMode] = useState<SortMode>("newest");
  const [selectedSources, setSelectedSources] = useState<string[]>([]);

  useEffect(() => {
    async function fetchData() {
      try {
        const response = await apiClient.get<DigestFeedResponse>("/digests/feed");
        setData(mapDigestFeedToFeedData(response.data));
      } catch (e) {
        setError(e instanceof Error ? e.message : "Unknown error");
      } finally {
        setLoading(false);
      }
    }
    fetchData();
  }, []);

  // Toggle a source in the filter
  const toggleSource = useCallback((source: string) => {
    setSelectedSources((prev) => {
      if (prev.includes(source)) {
        return prev.filter((s) => s !== source);
      }
      return [...prev, source];
    });
  }, []);

  // Clear all source filters
  const clearSourceFilter = useCallback(() => {
    setSelectedSources([]);
  }, []);

  // Filtered + sorted topics
  const filteredTopics = useMemo(() => {
    if (!data) return [];

    let topics = data.topics;

    // Category filter
    if (activeCategory !== "all") {
      topics = topics.filter((t) => t.category === activeCategory);
    }

    // Source filter
    if (selectedSources.length > 0) {
      topics = topics.filter((t) =>
        t.sources.some((s) => selectedSources.includes(s.name))
      );
    }

    // Sort
    const sorted = [...topics];
    switch (sortMode) {
      case "newest":
        sorted.sort((a, b) => (b.published || "").localeCompare(a.published || ""));
        break;
      case "oldest":
        sorted.sort((a, b) => (a.published || "").localeCompare(b.published || ""));
        break;
      case "most-sources":
        sorted.sort((a, b) => b.article_count - a.article_count);
        break;
    }

    return sorted;
  }, [data, activeCategory, selectedSources, sortMode]);

  const featuredTopic = useMemo(() => {
    // Pick the first multi-source topic with an image, or the first topic
    return (
      filteredTopics.find((t) => t.image && t.article_count > 1) ||
      filteredTopics.find((t) => t.image) ||
      filteredTopics[0] ||
      null
    );
  }, [filteredTopics]);

  const gridTopics = useMemo(() => {
    if (!featuredTopic) return filteredTopics;
    return filteredTopics.filter((t) => t.id !== featuredTopic.id);
  }, [filteredTopics, featuredTopic]);

  // Available sources (from data)
  const availableSources = useMemo(() => {
    if (!data) return [];
    return data.meta.sources.sort();
  }, [data]);

  return {
    data,
    loading,
    error,
    activeCategory,
    setActiveCategory,
    filteredTopics,
    featuredTopic,
    gridTopics,
    selectedTopic,
    setSelectedTopic,
    sortMode,
    setSortMode,
    selectedSources,
    toggleSource,
    clearSourceFilter,
    availableSources,
  };
}

// ─── Utilities ─────────────────────────────────────────────────────────────

export function formatTimeAgo(dateStr: string): string {
  return formatChinaDateTimeShort(dateStr);
}

export function getLangLabel(lang: string): string {
  const map: Record<string, string> = {
    en: "英文",
    ja: "日文",
    zh: "中文",
    fr: "法文",
    ko: "韩文",
  };
  return map[lang] || lang;
}
