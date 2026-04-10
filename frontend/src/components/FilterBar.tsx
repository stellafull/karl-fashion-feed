/*
 * FilterBar — Editorial Noir Design (v3)
 * Sort dropdown + Source filter pills
 * Placed between CategoryNav and main content
 */

import { useState, useRef, useEffect } from "react";
import {
  Filter,
  X,
  ChevronDown,
  Layers,
  ArrowUpDown,
} from "lucide-react";
import type { SortMode } from "@/hooks/useFeedData";
import { motion, AnimatePresence } from "framer-motion";

interface FilterBarProps {
  sortMode: SortMode;
  onSortChange: (mode: SortMode) => void;
  availableSources: string[];
  selectedSources: string[];
  onToggleSource: (source: string) => void;
  onClearSources: () => void;
  totalCount: number;
}

export default function FilterBar({
  sortMode,
  onSortChange,
  availableSources,
  selectedSources,
  onToggleSource,
  onClearSources,
  totalCount,
}: FilterBarProps) {
  const [sourceOpen, setSourceOpen] = useState(false);
  const sourceRef = useRef<HTMLDivElement>(null);

  // Close dropdowns on outside click
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (sourceRef.current && !sourceRef.current.contains(e.target as Node)) {
        setSourceOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const isPublishedSort = sortMode === "newest" || sortMode === "oldest";
  const publishedSortLabel =
    sortMode === "oldest" ? "发布时间 ↑" : "发布时间 ↓";

  return (
    <div className="flex flex-wrap items-center justify-between gap-4 rounded-[28px] border border-[#e4dccf] bg-[rgba(255,255,255,0.68)] px-4 py-3 shadow-[0_18px_40px_rgba(44,33,16,0.04)] backdrop-blur">
      {/* Left: topic count + active filters */}
      <div className="flex items-center gap-3">
        <span className="text-xs text-muted-foreground font-body">
          共 {totalCount} 个话题
        </span>
        {/* Active source filter pills */}
        {selectedSources.length > 0 && (
          <div className="flex items-center gap-1.5 flex-wrap">
            {selectedSources.map((src) => (
              <button
                key={src}
                onClick={() => onToggleSource(src)}
                className="flex items-center gap-1 rounded-full border border-[#d6bf97] bg-[#f4ead9] px-2.5 py-1 text-[10px] font-body text-[#8a6931] transition-colors hover:bg-[#efe1c8]"
              >
                {src}
                <X className="w-2.5 h-2.5" />
              </button>
            ))}
            <button
              onClick={onClearSources}
              className="text-[10px] font-body text-[#7f776e] transition-colors hover:text-[#2b241d] underline underline-offset-2"
            >
              清除筛选
            </button>
          </div>
        )}
      </div>

      {/* Right: sort + source filter buttons */}
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={() =>
            onSortChange(sortMode === "oldest" ? "newest" : "oldest")
          }
          className={`flex items-center gap-2 rounded-full border px-4 py-2.5 text-sm font-body shadow-[0_10px_24px_rgba(44,33,16,0.03)] transition-all ${
            isPublishedSort
              ? "border-[#d6bf97] bg-[#f4ead9] text-[#8a6931]"
              : "border-[#ddd4c7] bg-[rgba(255,255,255,0.92)] text-[#5f584f] hover:border-[#c8b18a] hover:text-[#2b241d]"
          }`}
        >
          <ArrowUpDown className="h-4 w-4" />
          <span>{publishedSortLabel}</span>
        </button>

        <button
          type="button"
          onClick={() => onSortChange("most-sources")}
          className={`flex items-center gap-2 rounded-full border px-4 py-2.5 text-sm font-body shadow-[0_10px_24px_rgba(44,33,16,0.03)] transition-all ${
            sortMode === "most-sources"
              ? "border-[#d6bf97] bg-[#f4ead9] text-[#8a6931]"
              : "border-[#ddd4c7] bg-[rgba(255,255,255,0.92)] text-[#5f584f] hover:border-[#c8b18a] hover:text-[#2b241d]"
          }`}
        >
          <Layers className="h-4 w-4" />
          <span>来源最多</span>
        </button>

        {/* Source filter dropdown */}
        <div ref={sourceRef} className="relative">
          <button
            onClick={() => setSourceOpen(!sourceOpen)}
            className={`flex items-center gap-2 rounded-full border px-4 py-2.5 text-sm font-body shadow-[0_10px_24px_rgba(44,33,16,0.03)] transition-all ${
              selectedSources.length > 0
                ? "border-[#d6bf97] bg-[#f4ead9] text-[#8a6931]"
                : "border-[#ddd4c7] bg-[rgba(255,255,255,0.92)] text-[#5f584f] hover:border-[#c8b18a] hover:text-[#2b241d]"
            }`}
          >
            <Filter className="h-4 w-4 text-[#8f7442]" />
            <span>来源</span>
            {selectedSources.length > 0 && (
              <span className="flex h-5 min-w-5 items-center justify-center rounded-full bg-[#c8a66a] px-1 text-[10px] font-bold text-[#1f1c18]">
                {selectedSources.length}
              </span>
            )}
            <ChevronDown
              className={`h-4 w-4 transition-transform duration-200 ${sourceOpen ? "rotate-180" : ""}`}
            />
          </button>

          <AnimatePresence>
            {sourceOpen && (
              <motion.div
                initial={{ opacity: 0, y: -4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -4 }}
                transition={{ duration: 0.15 }}
                className="absolute right-0 top-full z-50 mt-2 max-h-[320px] min-w-[240px] overflow-y-auto rounded-[24px] border border-[#e4dccf] bg-[rgba(255,255,255,0.96)] shadow-[0_24px_50px_rgba(44,33,16,0.12)] backdrop-blur"
              >
                {/* Header */}
                <div className="flex items-center justify-between border-b border-[#eee4d5] px-4 py-3">
                  <span className="text-[10px] font-body font-medium uppercase tracking-[0.2em] text-[#8a8378]">
                    信息来源
                  </span>
                  {selectedSources.length > 0 && (
                    <button
                      onClick={() => {
                        onClearSources();
                      }}
                      className="text-[10px] font-body text-[#8a6931] hover:underline"
                    >
                      全部清除
                    </button>
                  )}
                </div>
                {/* Source list */}
                {availableSources.map((src) => {
                  const isSelected = selectedSources.includes(src);
                  return (
                    <button
                      key={src}
                      onClick={() => onToggleSource(src)}
                      className={`flex w-full items-center gap-3 px-4 py-3 text-sm font-body transition-colors ${
                        isSelected
                          ? "bg-[#f4ead9] text-[#8a6931]"
                          : "text-[#6a6259] hover:bg-[#f7f1e7] hover:text-[#2b241d]"
                      }`}
                    >
                      <div
                        className={`flex h-4 w-4 flex-shrink-0 items-center justify-center rounded-full border ${
                          isSelected ? "border-[#c8a66a] bg-[#c8a66a]" : "border-[#d6cec1]"
                        }`}
                      >
                        {isSelected && (
                          <svg
                            className="h-2.5 w-2.5 text-[#1f1c18]"
                            fill="none"
                            viewBox="0 0 24 24"
                            stroke="currentColor"
                            strokeWidth={3}
                          >
                            <path
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              d="M5 13l4 4L19 7"
                            />
                          </svg>
                        )}
                      </div>
                      <span className="truncate">{src}</span>
                    </button>
                  );
                })}
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
}
