/*
 * FilterBar — Editorial Noir Design (v3)
 * Sort dropdown + Source filter pills
 * Placed between CategoryNav and main content
 */

import { useState, useRef, useEffect } from "react";
import {
  ArrowUpDown,
  Filter,
  X,
  ChevronDown,
  Clock,
  Layers,
  CalendarArrowUp,
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

const SORT_OPTIONS: { value: SortMode; label: string; icon: React.ElementType }[] = [
  { value: "newest", label: "最新发布", icon: Clock },
  { value: "oldest", label: "最早发布", icon: CalendarArrowUp },
  { value: "most-sources", label: "来源最多", icon: Layers },
];

export default function FilterBar({
  sortMode,
  onSortChange,
  availableSources,
  selectedSources,
  onToggleSource,
  onClearSources,
  totalCount,
}: FilterBarProps) {
  const [sortOpen, setSortOpen] = useState(false);
  const [sourceOpen, setSourceOpen] = useState(false);
  const sortRef = useRef<HTMLDivElement>(null);
  const sourceRef = useRef<HTMLDivElement>(null);

  // Close dropdowns on outside click
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (sortRef.current && !sortRef.current.contains(e.target as Node)) {
        setSortOpen(false);
      }
      if (sourceRef.current && !sourceRef.current.contains(e.target as Node)) {
        setSourceOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const currentSort = SORT_OPTIONS.find((o) => o.value === sortMode) || SORT_OPTIONS[0];

  return (
    <div className="flex items-center justify-between gap-4 flex-wrap">
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
                className="flex items-center gap-1 px-2 py-0.5 text-[10px] font-body bg-gold/10 text-gold border border-gold/30 hover:bg-gold/20 transition-colors"
              >
                {src}
                <X className="w-2.5 h-2.5" />
              </button>
            ))}
            <button
              onClick={onClearSources}
              className="text-[10px] font-body text-muted-foreground hover:text-foreground transition-colors underline underline-offset-2"
            >
              清除筛选
            </button>
          </div>
        )}
      </div>

      {/* Right: sort + source filter buttons */}
      <div className="flex items-center gap-2">
        {/* Sort dropdown */}
        <div ref={sortRef} className="relative">
          <button
            onClick={() => {
              setSortOpen(!sortOpen);
              setSourceOpen(false);
            }}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-body text-muted-foreground hover:text-foreground border border-border hover:border-gold/40 transition-all bg-card"
          >
            <ArrowUpDown className="w-3 h-3" />
            <span>{currentSort.label}</span>
            <ChevronDown
              className={`w-3 h-3 transition-transform duration-200 ${sortOpen ? "rotate-180" : ""}`}
            />
          </button>

          <AnimatePresence>
            {sortOpen && (
              <motion.div
                initial={{ opacity: 0, y: -4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -4 }}
                transition={{ duration: 0.15 }}
                className="absolute right-0 top-full mt-1 z-50 bg-card border border-border shadow-lg min-w-[140px]"
              >
                {SORT_OPTIONS.map((opt) => {
                  const Icon = opt.icon;
                  const isActive = sortMode === opt.value;
                  return (
                    <button
                      key={opt.value}
                      onClick={() => {
                        onSortChange(opt.value);
                        setSortOpen(false);
                      }}
                      className={`w-full flex items-center gap-2 px-3 py-2 text-xs font-body transition-colors ${
                        isActive
                          ? "text-gold bg-gold/5"
                          : "text-muted-foreground hover:text-foreground hover:bg-secondary/50"
                      }`}
                    >
                      <Icon className="w-3 h-3" />
                      <span>{opt.label}</span>
                    </button>
                  );
                })}
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {/* Source filter dropdown */}
        <div ref={sourceRef} className="relative">
          <button
            onClick={() => {
              setSourceOpen(!sourceOpen);
              setSortOpen(false);
            }}
            className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-body border transition-all bg-card ${
              selectedSources.length > 0
                ? "text-gold border-gold/40"
                : "text-muted-foreground hover:text-foreground border-border hover:border-gold/40"
            }`}
          >
            <Filter className="w-3 h-3" />
            <span>来源</span>
            {selectedSources.length > 0 && (
              <span className="w-4 h-4 flex items-center justify-center text-[9px] font-bold bg-gold text-ink">
                {selectedSources.length}
              </span>
            )}
            <ChevronDown
              className={`w-3 h-3 transition-transform duration-200 ${sourceOpen ? "rotate-180" : ""}`}
            />
          </button>

          <AnimatePresence>
            {sourceOpen && (
              <motion.div
                initial={{ opacity: 0, y: -4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -4 }}
                transition={{ duration: 0.15 }}
                className="absolute right-0 top-full mt-1 z-50 bg-card border border-border shadow-lg min-w-[200px] max-h-[320px] overflow-y-auto"
              >
                {/* Header */}
                <div className="flex items-center justify-between px-3 py-2 border-b border-border">
                  <span className="text-[10px] font-body font-medium text-muted-foreground uppercase tracking-wider">
                    信息来源
                  </span>
                  {selectedSources.length > 0 && (
                    <button
                      onClick={() => {
                        onClearSources();
                      }}
                      className="text-[10px] font-body text-gold hover:underline"
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
                      className={`w-full flex items-center gap-2 px-3 py-2 text-xs font-body transition-colors ${
                        isSelected
                          ? "text-gold bg-gold/5"
                          : "text-muted-foreground hover:text-foreground hover:bg-secondary/50"
                      }`}
                    >
                      <div
                        className={`w-3.5 h-3.5 border flex items-center justify-center flex-shrink-0 ${
                          isSelected ? "border-gold bg-gold" : "border-border"
                        }`}
                      >
                        {isSelected && (
                          <svg
                            className="w-2.5 h-2.5 text-ink"
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
