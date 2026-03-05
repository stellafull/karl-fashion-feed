/*
 * SourcesSidebar — Editorial Noir Design
 * Right sidebar showing data sources and update info
 */

import { Rss, Clock, Globe } from "lucide-react";
import type { FeedMeta } from "@/hooks/useFeedData";

interface SourcesSidebarProps {
  meta: FeedMeta;
}

const SOURCE_URLS: Record<string, string> = {
  Vogue: "https://www.vogue.com",
  "Vogue Fashion": "https://www.vogue.com/fashion",
  WWD: "https://wwd.com",
  Hypebeast: "https://hypebeast.com",
  "Hypebeast Fashion": "https://hypebeast.com/fashion",
  Highsnobiety: "https://www.highsnobiety.com",
  GQ: "https://www.gq.com",
  Elle: "https://www.elle.com",
  Fashionista: "https://fashionista.com",
  BOF: "https://www.businessoffashion.com",
  Dazed: "https://www.dazeddigital.com",
  "i-D": "https://i-d.co",
  "Harper's Bazaar": "https://www.harpersbazaar.com",
  "Fashion Dive": "https://www.fashiondive.com",
  "WWD Japan": "https://www.wwdjapan.com",
  "Vogue Japan": "https://www.vogue.co.jp",
};

export default function SourcesSidebar({ meta }: SourcesSidebarProps) {
  return (
    <aside className="space-y-6">
      {/* Update info */}
      <div className="p-4 border border-border bg-card">
        <div className="flex items-center gap-2 mb-3">
          <Clock className="w-4 h-4 text-gold" />
          <h3 className="font-body font-semibold text-sm">更新信息</h3>
        </div>
        <div className="space-y-2 text-xs text-muted-foreground font-body">
          <p>
            最近更新：
            {new Date(meta.generated_at).toLocaleString("zh-CN", {
              year: "numeric",
              month: "long",
              day: "numeric",
              hour: "2-digit",
              minute: "2-digit",
            })}
          </p>
          <p>共 {meta.total_articles} 篇资讯</p>
          <p>来自 {meta.sources_count} 个信息源</p>
        </div>
      </div>

      {/* Sources list */}
      <div className="p-4 border border-border bg-card">
        <div className="flex items-center gap-2 mb-3">
          <Globe className="w-4 h-4 text-gold" />
          <h3 className="font-body font-semibold text-sm">信息来源</h3>
        </div>
        <div className="space-y-0">
          {meta.sources.sort().map((source) => (
            <a
              key={source}
              href={SOURCE_URLS[source] || "#"}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-2 py-2 text-xs font-body text-muted-foreground hover:text-gold transition-colors duration-200 border-b border-border/50 last:border-0"
            >
              <Rss className="w-3 h-3 flex-shrink-0" />
              <span className="truncate">{source}</span>
            </a>
          ))}
        </div>
      </div>

      {/* About */}
      <div className="p-4 border border-border bg-card">
        <h3 className="font-body font-semibold text-sm mb-2">关于</h3>
        <p className="text-xs text-muted-foreground font-body leading-relaxed">
          Fashion Feed 聚合全球主流时尚媒体的最新资讯，通过 AI
          自动翻译、分类和摘要，为中文读者提供一站式时尚信息服务。每两小时自动更新。
        </p>
      </div>
    </aside>
  );
}
