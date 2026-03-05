/*
 * Header Component — Editorial Noir Design (v2: Topic mode)
 * Minimal top bar with brand identity and meta info
 */

import { Rss } from "lucide-react";
import type { FeedMeta } from "@/hooks/useFeedData";

interface HeaderProps {
  meta: FeedMeta | null;
}

export default function Header({ meta }: HeaderProps) {
  return (
    <header className="sticky top-0 z-50 bg-background/80 backdrop-blur-md border-b border-border">
      <div className="container flex items-center justify-between h-14 md:h-16">
        {/* Brand */}
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 bg-ink flex items-center justify-center">
            <Rss className="w-4 h-4 text-gold" />
          </div>
          <div>
            <h1 className="font-display text-lg md:text-xl font-bold tracking-tight leading-none">
              Fashion Feed
            </h1>
            <p className="text-[10px] md:text-xs text-muted-foreground tracking-[0.2em] uppercase font-body">
              时尚资讯聚合
            </p>
          </div>
        </div>

        {/* Meta info */}
        <div className="hidden sm:flex items-center gap-6 text-xs text-muted-foreground font-body">
          {meta && (
            <>
              <span className="flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-gold animate-pulse" />
                {meta.sources_count} 个来源
              </span>
              <span>{meta.total_topics} 个话题</span>
              <span>{meta.total_articles} 篇原文</span>
              <span>
                更新于{" "}
                {new Date(meta.generated_at).toLocaleString("zh-CN", {
                  month: "short",
                  day: "numeric",
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </span>
            </>
          )}
        </div>
      </div>
    </header>
  );
}
