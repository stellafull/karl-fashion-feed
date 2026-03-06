import { useState, useEffect, useMemo, useCallback } from "react";

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

export type SortMode = "newest" | "oldest" | "most-sources";

// ─── Hook ──────────────────────────────────────────────────────────────────

export function useFeedData() {
  const feedDataUrl = `${import.meta.env.BASE_URL}feed-data.json`;
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
        const resp = await fetch(feedDataUrl);
        if (!resp.ok) throw new Error("Failed to load feed data");
        const json: FeedData = await resp.json();
        setData(json);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Unknown error");
      } finally {
        setLoading(false);
      }
    }
    fetchData();
  }, [feedDataUrl]);

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
  if (!dateStr) return "";
  try {
    const date = new Date(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 1) return "刚刚";
    if (diffMins < 60) return `${diffMins}分钟前`;
    if (diffHours < 24) return `${diffHours}小时前`;
    if (diffDays < 7) return `${diffDays}天前`;
    return date.toLocaleDateString("zh-CN", { month: "short", day: "numeric" });
  } catch {
    return "";
  }
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
