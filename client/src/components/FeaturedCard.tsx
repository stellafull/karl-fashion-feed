/*
 * FeaturedCard — Editorial Noir Design
 * Large hero card with image background, gradient overlay, and editorial typography
 * The first and most prominent article in the feed
 */

import { Clock, ExternalLink, Layers } from "lucide-react";
import type { Article } from "@/hooks/useFeedData";
import { formatTimeAgo } from "@/hooks/useFeedData";
import { motion } from "framer-motion";

const FALLBACK_IMAGE =
  "https://d2xsxph8kpxj0f.cloudfront.net/310519663404425913/XsRzs3R3SMWpsb8CkVUfqq/hero-fashion-runway-ZtgpWi2MaKrrfhi6Fy6CcA.webp";

interface FeaturedCardProps {
  article: Article;
}

export default function FeaturedCard({ article }: FeaturedCardProps) {
  const imageUrl = article.image || FALLBACK_IMAGE;

  return (
    <motion.a
      href={article.link}
      target="_blank"
      rel="noopener noreferrer"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
      className="group relative block w-full aspect-[21/9] min-h-[280px] md:min-h-[360px] lg:min-h-[420px] overflow-hidden bg-ink"
    >
      {/* Background image */}
      <div
        className="absolute inset-0 bg-cover bg-center transition-transform duration-700 ease-out group-hover:scale-[1.03]"
        style={{ backgroundImage: `url(${imageUrl})` }}
      />

      {/* Gradient overlay */}
      <div className="absolute inset-0 bg-gradient-to-t from-black/90 via-black/40 to-transparent" />
      <div className="absolute inset-0 bg-gradient-to-r from-black/50 to-transparent" />

      {/* Content */}
      <div className="absolute inset-0 flex flex-col justify-end p-6 md:p-10 lg:p-14">
        {/* Category & Source badges */}
        <div className="flex items-center gap-3 mb-4">
          <span className="px-3 py-1 text-xs font-body font-medium tracking-wider uppercase bg-gold/90 text-ink">
            {article.category_name || "时尚"}
          </span>
          <span className="text-xs font-body text-white/70">
            {article.source}
          </span>
          {article.is_clustered && (
            <span className="flex items-center gap-1 text-xs text-white/70">
              <Layers className="w-3 h-3" />
              多源聚合
            </span>
          )}
        </div>

        {/* Title */}
        <h2 className="font-display text-2xl md:text-4xl lg:text-5xl font-bold text-white leading-tight max-w-3xl mb-3 md:mb-4">
          {article.title}
        </h2>

        {/* Summary */}
        <p className="font-body text-sm md:text-base text-white/80 leading-relaxed max-w-2xl mb-4 md:mb-6 line-clamp-2 md:line-clamp-3">
          {article.summary}
        </p>

        {/* Meta row */}
        <div className="flex items-center gap-4 text-xs text-white/60 font-body">
          <span className="flex items-center gap-1.5">
            <Clock className="w-3.5 h-3.5" />
            {formatTimeAgo(article.published)}
          </span>
          <span className="flex items-center gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity duration-300">
            <ExternalLink className="w-3.5 h-3.5" />
            阅读原文
          </span>
        </div>

        {/* Cluster sources */}
        {article.is_clustered && article.cluster_sources.length > 0 && (
          <div className="mt-3 flex items-center gap-2 flex-wrap">
            {article.cluster_sources.map((src, i) => (
              <span
                key={i}
                className="text-[10px] text-white/50 font-body border border-white/20 px-2 py-0.5"
              >
                {src.source}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Gold accent line at bottom */}
      <div className="absolute bottom-0 left-0 w-0 h-0.5 bg-gold transition-all duration-700 ease-out group-hover:w-full" />
    </motion.a>
  );
}
