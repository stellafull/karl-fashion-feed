/*
 * SourcesSidebar — Editorial Noir Design (v3: Luxury Brand Focus)
 * Right sidebar showing stats, sources (clickable for filtering), and about info
 */

import { Rss, Clock, Globe, Layers, BarChart3, Check } from "lucide-react";
import type { FeedMeta } from "@/hooks/useFeedData";
import { ScrollArea } from "@/components/ui/scroll-area";

interface SourcesSidebarProps {
  meta: FeedMeta;
  selectedSources: string[];
  onToggleSource: (source: string) => void;
}

const SOURCE_URLS: Record<string, string> = {
  Vogue: "https://www.vogue.com",
  "Vogue Fashion": "https://www.vogue.com/fashion",
  "Vogue Business": "https://www.voguebusiness.com",
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
  Glamour: "https://www.glamour.com",
  Refinery29: "https://www.refinery29.com",
  "The Cut": "https://www.thecut.com",
  "Who What Wear": "https://www.whowhatwear.com",
  Coveteur: "https://coveteur.com",
  Fashionsnap: "https://www.fashionsnap.com",
};

export default function SourcesSidebar({
  meta,
  selectedSources,
  onToggleSource,
}: SourcesSidebarProps) {
  const sortedSources = [...meta.sources].sort();

  return (
    <aside className="space-y-6 lg:flex lg:max-h-[calc(100vh-6rem)] lg:flex-col lg:gap-6 lg:space-y-0 lg:overflow-hidden">
      {/* Stats */}
      <div className="p-4 border border-border bg-card">
        <div className="flex items-center gap-2 mb-3">
          <BarChart3 className="w-4 h-4 text-gold" />
          <h3 className="font-body font-semibold text-sm">数据概览</h3>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div className="text-center p-3 bg-secondary/50">
            <div className="font-display text-2xl font-bold text-gold">
              {meta.total_topics}
            </div>
            <div className="text-[10px] text-muted-foreground font-body mt-0.5">
              话题
            </div>
          </div>
          <div className="text-center p-3 bg-secondary/50">
            <div className="font-display text-2xl font-bold text-foreground">
              {meta.total_articles}
            </div>
            <div className="text-[10px] text-muted-foreground font-body mt-0.5">
              原始文章
            </div>
          </div>
        </div>
      </div>

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
          <p className="flex items-center gap-1">
            <Layers className="w-3 h-3" />
            AI 自动聚合同话题报道
          </p>
        </div>
      </div>

      {/* Sources list — clickable for filtering */}
      <div className="p-4 border border-border bg-card lg:min-h-0 lg:flex-1 lg:flex lg:flex-col">
        <div className="flex items-center gap-2 mb-3">
          <Globe className="w-4 h-4 text-gold" />
          <h3 className="font-body font-semibold text-sm">
            信息来源 ({meta.sources_count})
          </h3>
        </div>
        <p className="text-[10px] text-muted-foreground font-body mb-2">
          点击来源可筛选相关话题
        </p>
        <ScrollArea className="pr-3 lg:min-h-0 lg:flex-1">
          <div className="space-y-0">
            {sortedSources.map((source) => {
            const isSelected = selectedSources.includes(source);
            return (
              <button
                key={source}
                onClick={() => onToggleSource(source)}
                className={`w-full flex items-center gap-2 py-2 text-xs font-body transition-colors duration-200 border-b border-border/50 last:border-0 text-left ${
                  isSelected
                    ? "text-gold"
                    : "text-muted-foreground hover:text-gold"
                }`}
              >
                {isSelected ? (
                  <Check className="w-3 h-3 flex-shrink-0 text-gold" />
                ) : (
                  <Rss className="w-3 h-3 flex-shrink-0" />
                )}
                <span className="truncate flex-1">{source}</span>
              </button>
            );
            })}
          </div>
        </ScrollArea>
      </div>

      {/* About */}
      <div className="p-4 border border-border bg-card">
        <h3 className="font-body font-semibold text-sm mb-2">关于</h3>
        <p className="text-xs text-muted-foreground font-body leading-relaxed">
          Fashion Feed 聚合全球主流时尚媒体的最新资讯，通过 AI
          自动识别同一话题的多源报道，生成综合性的中文摘要。
          无需翻墙即可一站式了解全球时尚动态。每两小时自动更新。
        </p>
      </div>
    </aside>
  );
}
