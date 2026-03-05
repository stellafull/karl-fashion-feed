import { useState, useEffect, useMemo } from "react";

export interface Article {
  id: string;
  title: string;
  title_original: string;
  summary: string;
  link: string;
  source: string;
  category: string;
  category_name: string;
  image: string;
  published: string;
  tags: string[];
  is_clustered: boolean;
  cluster_sources: { source: string; title: string; link: string }[];
}

export interface Category {
  id: string;
  name: string;
  icon: string;
}

export interface FeedMeta {
  generated_at: string;
  total_articles: number;
  sources_count: number;
  sources: string[];
}

export interface FeedData {
  meta: FeedMeta;
  categories: Category[];
  articles: Article[];
}

export function useFeedData() {
  const [data, setData] = useState<FeedData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeCategory, setActiveCategory] = useState("all");

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

  const filteredArticles = useMemo(() => {
    if (!data) return [];
    if (activeCategory === "all") return data.articles;
    return data.articles.filter((a) => a.category === activeCategory);
  }, [data, activeCategory]);

  const featuredArticle = useMemo(() => {
    // Pick the first article with an image as featured
    return filteredArticles.find((a) => a.image) || filteredArticles[0] || null;
  }, [filteredArticles]);

  const gridArticles = useMemo(() => {
    if (!featuredArticle) return filteredArticles;
    return filteredArticles.filter((a) => a.id !== featuredArticle.id);
  }, [filteredArticles, featuredArticle]);

  return {
    data,
    loading,
    error,
    activeCategory,
    setActiveCategory,
    filteredArticles,
    featuredArticle,
    gridArticles,
  };
}

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
