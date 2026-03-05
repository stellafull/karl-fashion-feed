/*
 * Home Page — Editorial Noir Design
 * Main feed page with Perplexity Discover-like layout
 * Structure: Header → Category Nav → Featured Hero → Article Grid + Sidebar
 */

import { useFeedData } from "@/hooks/useFeedData";
import Header from "@/components/Header";
import CategoryNav from "@/components/CategoryNav";
import FeaturedCard from "@/components/FeaturedCard";
import ArticleCard from "@/components/ArticleCard";
import SourcesSidebar from "@/components/SourcesSidebar";
import LoadingSkeleton from "@/components/LoadingSkeleton";
import { motion, AnimatePresence } from "framer-motion";
import { AlertCircle } from "lucide-react";

export default function Home() {
  const {
    data,
    loading,
    error,
    activeCategory,
    setActiveCategory,
    featuredArticle,
    gridArticles,
  } = useFeedData();

  if (loading) return <LoadingSkeleton />;

  if (error || !data) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="text-center space-y-4 p-8">
          <AlertCircle className="w-12 h-12 text-gold mx-auto" />
          <h2 className="font-display text-2xl font-bold">无法加载资讯</h2>
          <p className="font-body text-sm text-muted-foreground max-w-md">
            {error || "数据加载失败，请稍后刷新页面重试。"}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background">
      <Header meta={data.meta} />
      <CategoryNav
        categories={data.categories}
        activeCategory={activeCategory}
        onCategoryChange={setActiveCategory}
      />

      <main>
        {/* Featured article */}
        <AnimatePresence mode="wait">
          {featuredArticle && (
            <motion.div
              key={`featured-${activeCategory}-${featuredArticle.id}`}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.3 }}
              className="container mt-6"
            >
              <FeaturedCard article={featuredArticle} />
            </motion.div>
          )}
        </AnimatePresence>

        {/* Main content area: Grid + Sidebar */}
        <div className="container mt-8 mb-16">
          <div className="flex flex-col lg:flex-row gap-8">
            {/* Article grid — main content */}
            <div className="flex-1 min-w-0">
              {/* Section header */}
              <div className="flex items-center justify-between mb-6">
                <div className="flex items-center gap-3">
                  <div className="w-1 h-6 bg-gold" />
                  <h2 className="font-display text-xl font-bold">最新资讯</h2>
                </div>
                <span className="text-xs text-muted-foreground font-body">
                  共 {gridArticles.length} 篇
                </span>
              </div>

              <AnimatePresence mode="wait">
                <motion.div
                  key={`grid-${activeCategory}`}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -10 }}
                  transition={{ duration: 0.3 }}
                >
                  {/* First row: 2 wide cards */}
                  {gridArticles.length > 0 && (
                    <div className="space-y-5 mb-8">
                      {gridArticles.slice(0, 2).map((article, i) => (
                        <ArticleCard
                          key={article.id}
                          article={article}
                          index={i}
                          variant="wide"
                        />
                      ))}
                    </div>
                  )}

                  {/* Grid of default cards */}
                  {gridArticles.length > 2 && (
                    <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-5">
                      {gridArticles.slice(2, 14).map((article, i) => (
                        <ArticleCard
                          key={article.id}
                          article={article}
                          index={i + 2}
                          variant="default"
                        />
                      ))}
                    </div>
                  )}

                  {/* Compact list for remaining articles */}
                  {gridArticles.length > 14 && (
                    <div className="mt-8">
                      <div className="flex items-center gap-3 mb-4">
                        <div className="w-1 h-5 bg-border" />
                        <h3 className="font-body font-semibold text-sm text-muted-foreground">
                          更多资讯
                        </h3>
                      </div>
                      <div className="border border-border bg-card p-4">
                        {gridArticles.slice(14).map((article, i) => (
                          <ArticleCard
                            key={article.id}
                            article={article}
                            index={i + 14}
                            variant="compact"
                          />
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Empty state */}
                  {gridArticles.length === 0 && !featuredArticle && (
                    <div className="text-center py-16">
                      <p className="font-body text-muted-foreground">
                        该分类暂无资讯
                      </p>
                    </div>
                  )}
                </motion.div>
              </AnimatePresence>
            </div>

            {/* Sidebar */}
            <div className="w-full lg:w-72 xl:w-80 flex-shrink-0">
              <div className="lg:sticky lg:top-20">
                <SourcesSidebar meta={data.meta} />
              </div>
            </div>
          </div>
        </div>
      </main>

      {/* Footer */}
      <footer className="border-t border-border py-8">
        <div className="container">
          <div className="flex flex-col sm:flex-row items-center justify-between gap-4">
            <div className="flex items-center gap-2">
              <span className="font-display text-sm font-bold">Fashion Feed</span>
              <span className="text-xs text-muted-foreground font-body">
                时尚资讯聚合
              </span>
            </div>
            <p className="text-xs text-muted-foreground font-body text-center sm:text-right">
              内容版权归原始来源所有。AI 翻译与摘要仅供参考。
            </p>
          </div>
        </div>
      </footer>
    </div>
  );
}
