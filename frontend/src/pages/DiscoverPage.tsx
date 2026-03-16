import { ArrowUpRight, Clock3, Layers3, Share2 } from "lucide-react";
import type { FeedData, SortMode, Topic } from "@/hooks/useFeedData";
import { Button } from "@/components/ui/button";
import { formatChinaDateTimeShort } from "@/lib/time";
import DiscoverRail from "@/components/DiscoverRail";

interface DiscoverPageProps {
  data: FeedData;
  activeCategory: string;
  sortMode: SortMode;
  featuredTopic: Topic | null;
  gridTopics: Topic[];
  filteredTopics: Topic[];
  availableSources: string[];
  selectedSources: string[];
  onCategoryChange: (categoryId: string) => void;
  onSortChange: (mode: SortMode) => void;
  onToggleSource: (source: string) => void;
  onClearSources: () => void;
  onOpenStory: (storyId: string) => void;
}

function getUniqueSources(topic: Topic) {
  return topic.sources
    .map((source) => source.name)
    .filter((value, index, array) => array.indexOf(value) === index);
}

function LeadStoryCard({
  topic,
  onOpen,
}: {
  topic: Topic;
  onOpen: () => void;
}) {
  const uniqueSources = getUniqueSources(topic);

  return (
    <button
      type="button"
      onClick={onOpen}
      className="grid w-full gap-6 rounded-[32px] border border-[#e4dccf] bg-white p-5 text-left transition-colors hover:border-[#c8b18a] lg:grid-cols-[1.15fr_0.85fr]"
    >
      <div className="flex min-w-0 flex-col justify-between">
        <div>
          <p className="text-xs uppercase tracking-[0.24em] text-[#8a8378]">
            {topic.category_name}
          </p>
          <h2 className="mt-3 max-w-[16ch] font-display text-4xl leading-[1.02] text-[#2b241d] md:text-5xl">
            {topic.title}
          </h2>
          <p className="mt-4 max-w-[56ch] text-lg leading-8 text-[#5f584f]">
            {topic.summary}
          </p>
        </div>
        <div className="mt-6 flex flex-wrap items-center gap-4 text-sm text-[#71695f]">
          <span className="flex items-center gap-1.5">
            <Clock3 className="h-4 w-4 text-[#9f7d45]" />
            {formatChinaDateTimeShort(topic.published)}
          </span>
          <span className="flex items-center gap-1.5">
            <Layers3 className="h-4 w-4 text-[#9f7d45]" />
            {topic.article_count} sources
          </span>
          <span>{uniqueSources.slice(0, 3).join(" · ")}</span>
        </div>
      </div>

      <div className="overflow-hidden rounded-[24px] bg-[#ece6dc]">
        {topic.image ? (
          <img
            src={topic.image}
            alt=""
            className="h-full min-h-[280px] w-full object-cover"
            loading="lazy"
          />
        ) : (
          <div className="flex min-h-[280px] items-center justify-center bg-[radial-gradient(circle_at_top_left,#f4ecdd,transparent_48%),linear-gradient(135deg,#ebe3d5,#d8c6ad)]" />
        )}
      </div>
    </button>
  );
}

function GridStoryCard({
  topic,
  onOpen,
}: {
  topic: Topic;
  onOpen: () => void;
}) {
  const uniqueSources = getUniqueSources(topic);

  return (
    <button
      type="button"
      onClick={onOpen}
      className="group flex w-full flex-col overflow-hidden rounded-[28px] border border-[#e4dccf] bg-white text-left transition-colors hover:border-[#c8b18a]"
    >
      <div className="aspect-[1.2/1] overflow-hidden bg-[#ece6dc]">
        {topic.image ? (
          <img
            src={topic.image}
            alt=""
            className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-[1.03]"
            loading="lazy"
          />
        ) : (
          <div className="h-full w-full bg-[radial-gradient(circle_at_top,#f2ebdd,transparent_42%),linear-gradient(135deg,#ebe3d5,#d6c2a5)]" />
        )}
      </div>
      <div className="flex flex-1 flex-col px-4 pb-4 pt-3">
        <h3 className="font-display text-[1.75rem] leading-[1.08] text-[#2b241d]">
          {topic.title}
        </h3>
        <p className="mt-3 line-clamp-3 text-sm leading-6 text-[#665f56]">
          {topic.summary}
        </p>
        <div className="mt-auto flex items-center gap-2 pt-4 text-xs text-[#7d766d]">
          <span>{uniqueSources.slice(0, 2).join(" · ")}</span>
          <span className="text-[#c1b5a4]">•</span>
          <span>{topic.article_count} sources</span>
        </div>
      </div>
    </button>
  );
}

function RowStoryCard({
  topic,
  onOpen,
}: {
  topic: Topic;
  onOpen: () => void;
}) {
  const uniqueSources = getUniqueSources(topic);

  return (
    <button
      type="button"
      onClick={onOpen}
      className="grid w-full gap-4 rounded-[28px] border border-[#e4dccf] bg-white p-4 text-left transition-colors hover:border-[#c8b18a] md:grid-cols-[260px_minmax(0,1fr)]"
    >
      <div className="overflow-hidden rounded-[22px] bg-[#ece6dc]">
        {topic.image ? (
          <img
            src={topic.image}
            alt=""
            className="h-full min-h-[180px] w-full object-cover"
            loading="lazy"
          />
        ) : (
          <div className="h-full min-h-[180px] w-full bg-[radial-gradient(circle_at_top,#f2ebdd,transparent_42%),linear-gradient(135deg,#ebe3d5,#d6c2a5)]" />
        )}
      </div>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-3 text-xs uppercase tracking-[0.18em] text-[#8a8378]">
          <span>{topic.category_name}</span>
          <span className="text-[#c1b5a4]">•</span>
          <span>{formatChinaDateTimeShort(topic.published)}</span>
        </div>
        <h3 className="mt-3 font-display text-[2rem] leading-[1.08] text-[#2b241d]">
          {topic.title}
        </h3>
        <p className="mt-3 line-clamp-3 max-w-[60ch] text-base leading-7 text-[#605950]">
          {topic.summary}
        </p>
        <div className="mt-4 flex flex-wrap items-center gap-3 text-sm text-[#746d63]">
          <span>{uniqueSources.slice(0, 3).join(" · ")}</span>
          <span className="text-[#c1b5a4]">•</span>
          <span>{topic.article_count} sources</span>
          <span className="ml-auto flex items-center gap-1.5 text-[#9f7d45]">
            Open story
            <ArrowUpRight className="h-4 w-4" />
          </span>
        </div>
      </div>
    </button>
  );
}

const SORT_OPTIONS: Array<{ value: SortMode; label: string }> = [
  { value: "newest", label: "Latest" },
  { value: "oldest", label: "Oldest" },
  { value: "most-sources", label: "Most sources" },
];

export default function DiscoverPage({
  data,
  activeCategory,
  sortMode,
  featuredTopic,
  gridTopics,
  filteredTopics,
  availableSources,
  selectedSources,
  onCategoryChange,
  onSortChange,
  onToggleSource,
  onClearSources,
  onOpenStory,
}: DiscoverPageProps) {
  const listStories = gridTopics.slice(3);

  return (
    <div className="flex h-full min-h-0 flex-col bg-[#f7f3eb] text-[#1f1c18]">
      <header className="shrink-0 border-b border-[#e4dccf] bg-[#f7f3eb]/90 backdrop-blur">
        <div className="flex h-16 items-center justify-between px-5 md:px-8">
          <div>
            <p className="text-sm font-medium text-[#7f786f]">Discover</p>
          </div>
          <Button variant="outline" className="rounded-full bg-white">
            <Share2 className="h-4 w-4" />
            Share
          </Button>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto px-4 py-5 md:px-8">
        <div className="mb-6 flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
          <div className="space-y-3">
            <div className="flex flex-wrap gap-2">
              {data.categories.map((category) => {
                const active = activeCategory === category.id;
                return (
                  <button
                    key={category.id}
                    type="button"
                    onClick={() => onCategoryChange(category.id)}
                    className={`rounded-full border px-4 py-2 text-sm transition-colors ${
                      active
                        ? "border-[#1f1c18] bg-[#1f1c18] text-[#f4efe5]"
                        : "border-[#ddd4c7] bg-white text-[#6e665d] hover:border-[#bfa57b] hover:text-[#1f1c18]"
                    }`}
                  >
                    {category.name}
                  </button>
                );
              })}
            </div>
            <div className="flex flex-wrap items-center gap-3 text-sm text-[#786f64]">
              <span>{filteredTopics.length} stories</span>
              {selectedSources.length > 0 && (
                <span className="rounded-full bg-[#efe7d8] px-3 py-1 text-xs text-[#695d49]">
                  {selectedSources.join(" · ")}
                </span>
              )}
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            {SORT_OPTIONS.map((option) => {
              const active = sortMode === option.value;
              return (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => onSortChange(option.value)}
                  className={`rounded-full border px-3 py-2 text-sm transition-colors ${
                    active
                      ? "border-[#c8b18a] bg-[#efe7d8] text-[#1f1c18]"
                      : "border-[#ddd4c7] bg-white text-[#786f64] hover:border-[#c8b18a] hover:text-[#1f1c18]"
                  }`}
                >
                  {option.label}
                </button>
              );
            })}
          </div>
        </div>

        <div className="grid gap-8 xl:grid-cols-[minmax(0,1fr)_320px]">
          <div className="space-y-8">
            {featuredTopic ? (
              <LeadStoryCard
                topic={featuredTopic}
                onOpen={() => onOpenStory(featuredTopic.id)}
              />
            ) : (
              <div className="rounded-[28px] border border-dashed border-[#d6ccb9] px-8 py-20 text-center text-[#7c756b]">
                当前筛选下暂无 story。
              </div>
            )}

            {gridTopics.length > 0 && (
              <div className="grid gap-5 lg:grid-cols-3">
                {gridTopics.slice(0, 3).map((topic) => (
                  <GridStoryCard
                    key={topic.id}
                    topic={topic}
                    onOpen={() => onOpenStory(topic.id)}
                  />
                ))}
              </div>
            )}

            {listStories.length > 0 && (
              <div className="space-y-4">
                {listStories.map((topic) => (
                  <RowStoryCard
                    key={topic.id}
                    topic={topic}
                    onOpen={() => onOpenStory(topic.id)}
                  />
                ))}
              </div>
            )}
          </div>

          <div className="hidden xl:block">
            <div className="sticky top-6">
              <DiscoverRail
                meta={data.meta}
                topics={filteredTopics}
                availableSources={availableSources}
                selectedSources={selectedSources}
                onToggleSource={onToggleSource}
                onClearSources={onClearSources}
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
