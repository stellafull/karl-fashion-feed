/*
 * TopicDetail — Editorial Noir Design (v2)
 * Full-page overlay / panel showing the complete topic content
 * Replaces external link navigation — users read everything here
 */

import {
  ArrowLeft,
  Clock,
  Layers,
  ExternalLink,
  Globe,
  Tag,
  ChevronRight,
  ImageOff,
} from "lucide-react";
import type { Topic } from "@/hooks/useFeedData";
import { getLangLabel } from "@/hooks/useFeedData";
import { motion, AnimatePresence } from "framer-motion";
import { useEffect, useState } from "react";
import { formatChinaDateTimeShort } from "@/lib/time";

interface TopicDetailProps {
  topic: Topic | null;
  onClose: () => void;
}

export default function TopicDetail({ topic, onClose }: TopicDetailProps) {
  const [imageBroken, setImageBroken] = useState(false);

  useEffect(() => {
    setImageBroken(false);
  }, [topic?.id]);

  if (!topic) return null;

  const hasImage = Boolean(topic.image) && !imageBroken;
  const uniqueSources = topic.sources
    .map((s) => s.name)
    .filter((v, i, a) => a.indexOf(v) === i);

  return (
    <AnimatePresence>
      {topic && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.25 }}
          className="fixed inset-0 z-[100] bg-black/60 backdrop-blur-sm"
          onClick={onClose}
        >
          <motion.div
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ type: "spring", damping: 30, stiffness: 300 }}
            onClick={(e) => e.stopPropagation()}
            className="absolute right-0 top-0 bottom-0 w-full max-w-2xl bg-background overflow-y-auto"
          >
            {/* Hero image */}
            <div className="relative aspect-[16/9] overflow-hidden bg-ink">
              {hasImage ? (
                <img
                  src={topic.image}
                  alt=""
                  className="w-full h-full object-cover"
                  onError={() => setImageBroken(true)}
                />
              ) : (
                <div className="w-full h-full bg-[radial-gradient(circle_at_18%_22%,hsl(var(--secondary))_0%,transparent_52%),radial-gradient(circle_at_82%_78%,hsl(var(--muted))_0%,transparent_48%),linear-gradient(135deg,hsl(var(--muted))_0%,hsl(var(--secondary))_100%)] flex items-center justify-center">
                  <span className="flex items-center gap-2 text-white/70 text-sm font-body">
                    <ImageOff className="w-4 h-4" />
                    暂无配图
                  </span>
                </div>
              )}
              <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-black/20 to-transparent" />

              {/* Back button */}
              <button
                onClick={onClose}
                className="absolute top-4 left-4 flex items-center gap-2 px-3 py-2 bg-black/50 backdrop-blur-sm text-white/90 text-sm font-body hover:bg-black/70 transition-colors"
              >
                <ArrowLeft className="w-4 h-4" />
                返回
              </button>

              {/* Category badge */}
              <div className="absolute bottom-4 left-6">
                <span className="px-3 py-1 text-xs font-body font-medium tracking-wider uppercase bg-gold/90 text-ink">
                  {topic.category_name}
                </span>
              </div>
            </div>

            {/* Content area */}
            <div className="px-6 md:px-10 py-8">
              {/* Title */}
              <h1 className="font-display text-2xl md:text-3xl font-bold text-foreground leading-tight mb-4">
                {topic.title}
              </h1>

              {/* Meta bar */}
              <div className="flex items-center gap-4 mb-6 text-xs text-muted-foreground font-body flex-wrap">
                <span className="flex items-center gap-1.5">
                  <Clock className="w-3.5 h-3.5" />
                  {formatChinaDateTimeShort(topic.published)}
                </span>
                {topic.article_count > 1 && (
                  <span className="flex items-center gap-1.5 text-gold/80">
                    <Layers className="w-3.5 h-3.5" />
                    综合 {topic.article_count} 篇报道
                  </span>
                )}
                <span className="flex items-center gap-1.5">
                  <Globe className="w-3.5 h-3.5" />
                  {uniqueSources.length} 个来源
                </span>
              </div>

              {/* Divider */}
              <div className="w-12 h-0.5 bg-gold mb-8" />

              {/* Summary — the main content */}
              <div className="font-body text-base text-foreground leading-[1.9] mb-8 whitespace-pre-line">
                {topic.summary}
              </div>

              {/* Key points */}
              {topic.key_points.length > 0 && (
                <div className="mb-8 p-5 bg-secondary/50 border-l-2 border-gold">
                  <h3 className="font-body font-semibold text-sm text-foreground mb-3 flex items-center gap-2">
                    <ChevronRight className="w-4 h-4 text-gold" />
                    核心要点
                  </h3>
                  <ul className="space-y-2">
                    {topic.key_points.map((point, i) => (
                      <li
                        key={i}
                        className="font-body text-sm text-muted-foreground leading-relaxed pl-4 relative before:content-[''] before:absolute before:left-0 before:top-2 before:w-1.5 before:h-1.5 before:bg-gold/60"
                      >
                        {point}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Tags */}
              {topic.tags.length > 0 && (
                <div className="flex items-center gap-2 mb-8 flex-wrap">
                  <Tag className="w-3.5 h-3.5 text-muted-foreground" />
                  {topic.tags.map((tag, i) => (
                    <span
                      key={i}
                      className="text-xs font-body text-muted-foreground border border-border px-2 py-0.5 hover:border-gold/40 transition-colors"
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              )}

              {/* Sources section */}
              <div className="border-t border-border pt-6">
                <h3 className="font-body font-semibold text-sm text-foreground mb-4 flex items-center gap-2">
                  <Globe className="w-4 h-4 text-gold" />
                  原始来源 ({topic.sources.length})
                </h3>
                <div className="space-y-0">
                  {topic.sources.map((src, i) => (
                    <a
                      key={i}
                      href={src.link}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="group/src flex items-start gap-3 py-3 border-b border-border/50 last:border-0 hover:bg-secondary/30 transition-colors px-2 -mx-2"
                    >
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-0.5">
                          <span className="text-[10px] font-body font-medium text-gold">
                            {src.name}
                          </span>
                          <span className="text-[10px] font-body text-muted-foreground/60">
                            {getLangLabel(src.lang)}
                          </span>
                        </div>
                        <p className="font-body text-sm text-muted-foreground leading-snug line-clamp-1 group-hover/src:text-foreground transition-colors">
                          {src.title}
                        </p>
                      </div>
                      <ExternalLink className="w-3.5 h-3.5 text-muted-foreground/40 group-hover/src:text-gold transition-colors mt-1 flex-shrink-0" />
                    </a>
                  ))}
                </div>
              </div>

              {/* Disclaimer */}
              <div className="mt-8 pt-4 border-t border-border">
                <p className="text-[11px] text-muted-foreground/60 font-body leading-relaxed">
                  本文由 AI 综合多个来源自动生成，仅供参考。内容版权归各原始来源所有。
                  如需查看原文，请点击上方来源链接。
                </p>
              </div>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
