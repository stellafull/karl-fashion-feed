import { useState, useEffect, useMemo } from "react";

// ─── Data Types (v2: Topic-based) ──────────────────────────────────────────

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

// ─── Hook ──────────────────────────────────────────────────────────────────

export function useFeedData() {
  const [data, setData] = useState<FeedData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeCategory, setActiveCategory] = useState("all");
  const [selectedTopic, setSelectedTopic] = useState<Topic | null>(null);

  useEffect(() => {
    async function fetchData() {
      try {
        const resp = await fetch("/feed-data.json");
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
  }, []);

  const filteredTopics = useMemo(() => {
    if (!data) return [];
    if (activeCategory === "all") return data.topics;
    return data.topics.filter((t) => t.category === activeCategory);
  }, [data, activeCategory]);

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
