/*
 * FeaturedCard — Editorial Noir Design (v2: Topic mode)
 * Large hero card for the featured topic cluster
 * Clicking opens the topic detail panel instead of external link
 */

import { Clock, Layers, ChevronRight, ImageOff } from "lucide-react";
import type { Topic } from "@/hooks/useFeedData";
import { motion } from "framer-motion";
import { useState } from "react";
import { formatChinaDateTimeShort } from "@/lib/time";

interface FeaturedCardProps {
  topic: Topic;
  onClick: () => void;
}

export default function FeaturedCard({ topic, onClick }: FeaturedCardProps) {
  const [imageBroken, setImageBroken] = useState(false);
  const hasImage = Boolean(topic.image) && !imageBroken;

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
      onClick={onClick}
      className="group relative block w-full aspect-[21/9] min-h-[280px] md:min-h-[360px] lg:min-h-[420px] overflow-hidden bg-ink cursor-pointer"
    >
      {/* Background image */}
      {hasImage ? (
        <img
          src={topic.image}
          alt=""
          className="absolute inset-0 w-full h-full object-cover transition-transform duration-700 ease-out group-hover:scale-[1.03]"
          onError={() => setImageBroken(true)}
        />
      ) : (
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_18%_22%,hsl(var(--secondary))_0%,transparent_52%),radial-gradient(circle_at_82%_78%,hsl(var(--muted))_0%,transparent_48%),linear-gradient(135deg,hsl(var(--muted))_0%,hsl(var(--secondary))_100%)]" />
      )}

      {/* Gradient overlay */}
      <div className="absolute inset-0 bg-gradient-to-t from-black/90 via-black/40 to-transparent" />
      <div className="absolute inset-0 bg-gradient-to-r from-black/50 to-transparent" />

      {/* Content */}
      <div className="absolute inset-0 flex flex-col justify-end p-6 md:p-10 lg:p-14">
        {/* Category & Source badges */}
        <div className="flex items-center gap-3 mb-4">
          <span className="px-3 py-1 text-xs font-body font-medium tracking-wider uppercase bg-gold/90 text-ink">
            {topic.category_name || "时尚"}
          </span>
          {!hasImage && (
            <span className="flex items-center gap-1 text-xs text-white/60">
              <ImageOff className="w-3 h-3" />
              无配图
            </span>
          )}
          {topic.article_count > 1 && (
            <span className="flex items-center gap-1 text-xs text-white/70">
              <Layers className="w-3 h-3" />
              {topic.article_count} 篇来源聚合
            </span>
          )}
          {topic.sources.length > 0 && (
            <span className="text-xs font-body text-white/60">
              {topic.sources
                .map((s) => s.name)
                .filter((v, i, a) => a.indexOf(v) === i)
                .slice(0, 3)
                .join(" · ")}
            </span>
          )}
        </div>

        {/* Title */}
        <h2 className="font-display text-2xl md:text-4xl lg:text-5xl font-bold text-white leading-tight max-w-3xl mb-3 md:mb-4">
          {topic.title}
        </h2>

        {/* Summary preview */}
        <p className="font-body text-sm md:text-base text-white/80 leading-relaxed max-w-2xl mb-4 md:mb-6 line-clamp-2 md:line-clamp-3">
          {topic.summary}
        </p>

        {/* Meta row */}
        <div className="flex items-center gap-4 text-xs text-white/60 font-body">
          <span className="flex items-center gap-1.5">
            <Clock className="w-3.5 h-3.5" />
            {formatChinaDateTimeShort(topic.published)}
          </span>
          <span className="flex items-center gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity duration-300 text-gold">
            <ChevronRight className="w-3.5 h-3.5" />
            阅读详情
          </span>
        </div>
      </div>

      {/* Gold accent line at bottom */}
      <div className="absolute bottom-0 left-0 w-0 h-0.5 bg-gold transition-all duration-700 ease-out group-hover:w-full" />
    </motion.div>
  );
}
