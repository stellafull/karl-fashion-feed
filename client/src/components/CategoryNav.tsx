/*
 * CategoryNav — Editorial Noir Design (v3: Luxury Brand Focus)
 * Horizontal category tabs with gold accent underline
 * Categories: 全部, 秀场/系列, 街拍/造型, 趋势总结, 品牌/市场
 */

import { Newspaper, Sparkles, Camera, TrendingUp, Building2 } from "lucide-react";
import type { Category } from "@/hooks/useFeedData";
import { motion } from "framer-motion";

const iconMap: Record<string, React.ElementType> = {
  Newspaper,
  Sparkles,
  Camera,
  TrendingUp,
  Building2,
};

interface CategoryNavProps {
  categories: Category[];
  activeCategory: string;
  onCategoryChange: (id: string) => void;
}

export default function CategoryNav({
  categories,
  activeCategory,
  onCategoryChange,
}: CategoryNavProps) {
  return (
    <nav className="border-b border-border">
      <div className="container">
        <div className="flex items-center gap-1 overflow-x-auto scrollbar-hide py-1 -mb-px">
          {categories.map((cat) => {
            const Icon = iconMap[cat.icon] || Newspaper;
            const isActive = activeCategory === cat.id;

            return (
              <button
                key={cat.id}
                onClick={() => onCategoryChange(cat.id)}
                className={`
                  relative flex items-center gap-2 px-4 py-3 text-sm font-body font-medium
                  whitespace-nowrap transition-colors duration-300
                  ${
                    isActive
                      ? "text-foreground"
                      : "text-muted-foreground hover:text-foreground/80"
                  }
                `}
              >
                <Icon className="w-4 h-4" />
                <span>{cat.name}</span>
                {isActive && (
                  <motion.div
                    layoutId="category-underline"
                    className="absolute bottom-0 left-0 right-0 h-0.5 bg-gold"
                    transition={{ type: "spring", stiffness: 400, damping: 30 }}
                  />
                )}
              </button>
            );
          })}
        </div>
      </div>
    </nav>
  );
}
