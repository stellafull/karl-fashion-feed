/*
 * TopicCard — Editorial Noir Design (v2: Topic mode)
 * Card for displaying a topic cluster
 * Variants: wide (top 2), default (grid), compact (list)
 * Clicking opens topic detail panel
 */

import { Clock, Layers, ChevronRight } from "lucide-react";
import type { Topic } from "@/hooks/useFeedData";
import { formatTimeAgo } from "@/hooks/useFeedData";
import { motion } from "framer-motion";

const FALLBACK_IMAGE =
  "https://d2xsxph8kpxj0f.cloudfront.net/310519663404425913/XsRzs3R3SMWpsb8CkVUfqq/fallback-pattern-diwxooD7YAXKmRVyMjUGPW.webp";

interface TopicCardProps {
  topic: Topic;
  index: number;
  variant?: "default" | "compact" | "wide";
  onClick: () => void;
}

export default function TopicCard({
  topic,
  index,
  variant = "default",
  onClick,
}: TopicCardProps) {
  const imageUrl = topic.image || FALLBACK_IMAGE;
  const uniqueSources = topic.sources
    .map((s) => s.name)
    .filter((v, i, a) => a.indexOf(v) === i);

  if (variant === "compact") {
    return (
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, delay: Math.min(index * 0.03, 0.5), ease: [0.22, 1, 0.36, 1] }}
        onClick={onClick}
        className="group flex gap-4 py-4 border-b border-border last:border-0 hover:bg-secondary/30 transition-colors duration-300 px-2 -mx-2 cursor-pointer"
      >
        {/* Thumbnail */}
        <div className="w-20 h-20 flex-shrink-0 overflow-hidden bg-muted">
          <img
            src={imageUrl}
            alt=""
            className="w-full h-full object-cover transition-transform duration-500 group-hover:scale-110"
            loading="lazy"
            onError={(e) => {
              (e.target as HTMLImageElement).src = FALLBACK_IMAGE;
            }}
          />
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <h3 className="font-body font-semibold text-sm text-foreground leading-snug line-clamp-2 group-hover:text-gold transition-colors duration-300">
            {topic.title}
          </h3>
          <div className="flex items-center gap-2 mt-1.5 text-[11px] text-muted-foreground font-body">
            <span>{uniqueSources.slice(0, 2).join(" · ")}</span>
            <span className="text-border">|</span>
            <span>{formatTimeAgo(topic.published)}</span>
            {topic.article_count > 1 && (
              <>
                <span className="text-border">|</span>
                <span className="flex items-center gap-0.5 text-gold/70">
                  <Layers className="w-2.5 h-2.5" />
                  {topic.article_count}
                </span>
              </>
            )}
          </div>
        </div>
      </motion.div>
    );
  }

  if (variant === "wide") {
    return (
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, delay: Math.min(index * 0.05, 0.5), ease: [0.22, 1, 0.36, 1] }}
        onClick={onClick}
        className="group flex flex-col sm:flex-row gap-5 bg-card border border-border hover:border-gold/40 transition-all duration-400 overflow-hidden cursor-pointer"
      >
        {/* Image */}
        <div className="sm:w-64 md:w-80 aspect-[16/10] sm:aspect-auto overflow-hidden bg-muted flex-shrink-0">
          <img
            src={imageUrl}
            alt=""
            className="w-full h-full object-cover transition-transform duration-700 ease-out group-hover:scale-105"
            loading="lazy"
            onError={(e) => {
              (e.target as HTMLImageElement).src = FALLBACK_IMAGE;
            }}
          />
        </div>

        {/* Content */}
        <div className="flex-1 p-5 sm:py-5 sm:pr-5 sm:pl-0 flex flex-col justify-between">
          <div>
            <div className="flex items-center gap-2 mb-2">
              <span className="text-[10px] font-body font-medium tracking-wider uppercase text-gold">
                {topic.category_name}
              </span>
              {topic.article_count > 1 && (
                <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                  <Layers className="w-3 h-3" />
                  {topic.article_count} 篇聚合
                </span>
              )}
            </div>
            <h3 className="font-display text-lg md:text-xl font-bold text-foreground leading-snug mb-2 group-hover:text-gold transition-colors duration-300">
              {topic.title}
            </h3>
            <p className="font-body text-sm text-muted-foreground leading-relaxed line-clamp-2">
              {topic.summary}
            </p>
          </div>

          <div className="flex items-center gap-3 mt-4 text-xs text-muted-foreground font-body">
            <span>{uniqueSources.slice(0, 3).join(" · ")}</span>
            <span className="flex items-center gap-1">
              <Clock className="w-3 h-3" />
              {formatTimeAgo(topic.published)}
            </span>
            <span className="ml-auto flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity duration-300 text-gold">
              <ChevronRight className="w-3 h-3" />
              详情
            </span>
          </div>
        </div>
      </motion.div>
    );
  }

  // Default card
  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay: Math.min(index * 0.03, 0.5), ease: [0.22, 1, 0.36, 1] }}
      onClick={onClick}
      className="group flex flex-col bg-card border border-border hover:border-gold/40 transition-all duration-400 overflow-hidden cursor-pointer"
    >
      {/* Image */}
      <div className="aspect-[4/3] overflow-hidden bg-muted relative">
        <img
          src={imageUrl}
          alt=""
          className="w-full h-full object-cover transition-transform duration-700 ease-out group-hover:scale-105"
          loading="lazy"
          onError={(e) => {
            (e.target as HTMLImageElement).src = FALLBACK_IMAGE;
          }}
        />
        {/* Source badge */}
        {topic.article_count > 1 && (
          <div className="absolute top-3 left-3">
            <span className="px-2 py-0.5 text-[10px] font-body font-medium bg-black/60 text-white/90 backdrop-blur-sm flex items-center gap-1">
              <Layers className="w-2.5 h-2.5" />
              {topic.article_count} 篇聚合
            </span>
          </div>
        )}
        {topic.article_count === 1 && (
          <div className="absolute top-3 left-3">
            <span className="px-2 py-0.5 text-[10px] font-body font-medium bg-black/60 text-white/90 backdrop-blur-sm">
              {uniqueSources[0]}
            </span>
          </div>
        )}
      </div>

      {/* Content */}
      <div className="flex-1 p-4 flex flex-col">
        <div className="flex items-center gap-2 mb-2">
          <span className="text-[10px] font-body font-medium tracking-wider uppercase text-gold">
            {topic.category_name}
          </span>
        </div>

        <h3 className="font-display text-base md:text-lg font-bold text-foreground leading-snug mb-2 group-hover:text-gold transition-colors duration-300 line-clamp-2">
          {topic.title}
        </h3>

        <p className="font-body text-xs text-muted-foreground leading-relaxed line-clamp-3 flex-1">
          {topic.summary}
        </p>

        {/* Tags */}
        {topic.tags.length > 0 && (
          <div className="flex items-center gap-1.5 mt-3 flex-wrap">
            {topic.tags.slice(0, 3).map((tag, i) => (
              <span
                key={i}
                className="text-[10px] font-body text-muted-foreground border border-border px-1.5 py-0.5"
              >
                {tag}
              </span>
            ))}
          </div>
        )}

        {/* Meta */}
        <div className="flex items-center gap-2 mt-3 pt-3 border-t border-border text-[11px] text-muted-foreground font-body">
          <Clock className="w-3 h-3" />
          <span>{formatTimeAgo(topic.published)}</span>
          <span className="ml-auto text-[10px]">
            {uniqueSources.slice(0, 2).join(" · ")}
          </span>
        </div>
      </div>
    </motion.div>
  );
}
