import { Clock3, Filter, Layers3, Sparkles } from "lucide-react";
import type { FeedMeta, Topic } from "@/hooks/useFeedData";
import { formatChinaDateTimeFull } from "@/lib/time";

interface DiscoverRailProps {
  meta: FeedMeta;
  topics: Topic[];
  availableSources: string[];
  selectedSources: string[];
  onToggleSource: (source: string) => void;
  onClearSources: () => void;
}

function buildTopTags(topics: Topic[]) {
  const counts = new Map<string, number>();

  for (const topic of topics) {
    for (const tag of topic.tags) {
      counts.set(tag, (counts.get(tag) ?? 0) + 1);
    }
  }

  return Array.from(counts.entries())
    .sort((left, right) => right[1] - left[1])
    .slice(0, 6);
}

export default function DiscoverRail({
  meta,
  topics,
  availableSources,
  selectedSources,
  onToggleSource,
  onClearSources,
}: DiscoverRailProps) {
  const topTags = buildTopTags(topics);

  return (
    <aside className="space-y-4">
      <section className="rounded-[28px] border border-[#e4dccf] bg-white p-5">
        <div className="flex items-center gap-2 text-sm font-medium text-[#1f1c18]">
          <Layers3 className="h-4 w-4 text-[#9f7d45]" />
          Feed Snapshot
        </div>
        <div className="mt-4 grid grid-cols-2 gap-3">
          <div className="rounded-2xl bg-[#f4efe5] px-4 py-3">
            <p className="text-xs uppercase tracking-[0.18em] text-[#8a8378]">Topics</p>
            <p className="mt-2 font-display text-3xl">{meta.total_topics}</p>
          </div>
          <div className="rounded-2xl bg-[#f4efe5] px-4 py-3">
            <p className="text-xs uppercase tracking-[0.18em] text-[#8a8378]">Sources</p>
            <p className="mt-2 font-display text-3xl">{meta.sources_count}</p>
          </div>
        </div>
        <div className="mt-4 flex items-start gap-2 rounded-2xl bg-[#faf7f1] px-4 py-3 text-sm text-[#71695f]">
          <Clock3 className="mt-0.5 h-4 w-4 text-[#9f7d45]" />
          <div>
            <p>最近更新</p>
            <p className="mt-1 text-xs leading-relaxed">
              {formatChinaDateTimeFull(meta.generated_at)}
            </p>
          </div>
        </div>
      </section>

      <section className="rounded-[28px] border border-[#e4dccf] bg-white p-5">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-sm font-medium text-[#1f1c18]">
            <Filter className="h-4 w-4 text-[#9f7d45]" />
            Source Signals
          </div>
          {selectedSources.length > 0 && (
            <button
              type="button"
              onClick={onClearSources}
              className="text-xs text-[#7c756b] underline underline-offset-2"
            >
              清除
            </button>
          )}
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          {availableSources.slice(0, 14).map((source) => {
            const active = selectedSources.includes(source);
            return (
              <button
                key={source}
                type="button"
                onClick={() => onToggleSource(source)}
                className={`rounded-full border px-3 py-1.5 text-xs transition-colors ${
                  active
                    ? "border-[#1f1c18] bg-[#1f1c18] text-[#f4efe5]"
                    : "border-[#ddd4c7] text-[#6f685f] hover:border-[#bfa57b] hover:text-[#1f1c18]"
                }`}
              >
                {source}
              </button>
            );
          })}
        </div>
      </section>

      <section className="rounded-[28px] border border-[#e4dccf] bg-white p-5">
        <div className="flex items-center gap-2 text-sm font-medium text-[#1f1c18]">
          <Sparkles className="h-4 w-4 text-[#9f7d45]" />
          Topic Signals
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          {topTags.map(([tag, count]) => (
            <span
              key={tag}
              className="rounded-full border border-[#e4dccf] bg-[#faf7f1] px-3 py-1.5 text-xs text-[#6f685f]"
            >
              {tag} · {count}
            </span>
          ))}
        </div>
      </section>
    </aside>
  );
}
