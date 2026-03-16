import { Compass, Clock3, PanelLeft, PanelLeftClose, SquarePen } from "lucide-react";
import type { AiSession } from "@/lib/ai-demo";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { formatChinaDateTimeShort } from "@/lib/time";

interface AppSidebarProps {
  expanded: boolean;
  mobile?: boolean;
  sessions: AiSession[];
  activePath: string;
  onExpandedChange: (expanded: boolean) => void;
  onOpenDiscover: () => void;
  onOpenNewChat: () => void;
  onOpenHistory: () => void;
  onSelectSession: (sessionId: string) => void;
}

function RailButton({
  active,
  label,
  icon: Icon,
  onClick,
}: {
  active?: boolean;
  label: string;
  icon: React.ElementType;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex h-11 w-11 items-center justify-center rounded-2xl transition-colors",
        active
          ? "bg-[#1f1c18] text-[#f4efe5]"
          : "text-[#6f685f] hover:bg-[#ece6dc] hover:text-[#1f1c18]"
      )}
      aria-label={label}
      title={label}
    >
      <Icon className="h-4.5 w-4.5" />
    </button>
  );
}

export default function AppSidebar({
  expanded,
  mobile = false,
  sessions,
  activePath,
  onExpandedChange,
  onOpenDiscover,
  onOpenNewChat,
  onOpenHistory,
  onSelectSession,
}: AppSidebarProps) {
  if (mobile) {
    return (
      <div className="flex h-full flex-col bg-[#f7f3eb] text-[#1f1c18]">
        <div className="border-b border-[#e4dccf] px-4 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-[#1f1c18] text-sm font-semibold text-[#f4efe5]">
              FF
            </div>
            <div>
              <p className="font-display text-xl font-semibold">Fashion Feed</p>
              <p className="text-xs text-[#7c756b]">Discover / Chat / Story</p>
            </div>
          </div>
        </div>

        <div className="space-y-2 border-b border-[#e4dccf] px-4 py-4">
          <Button variant="outline" className="w-full justify-start" onClick={onOpenDiscover}>
            <Compass className="h-4 w-4" />
            Discover
          </Button>
          <Button variant="outline" className="w-full justify-start" onClick={onOpenNewChat}>
            <SquarePen className="h-4 w-4" />
            New chat
          </Button>
        </div>

        <div className="flex-1 overflow-y-auto px-4 py-4">
          <div className="mb-3 flex items-center gap-2 text-xs uppercase tracking-[0.24em] text-[#8b8479]">
            <Clock3 className="h-3.5 w-3.5" />
            Recent
          </div>
          <div className="space-y-2">
            {sessions.map((session) => (
              <button
                key={session.id}
                type="button"
                onClick={() => onSelectSession(session.id)}
                className={cn(
                  "w-full rounded-2xl border px-3 py-3 text-left transition-colors",
                  activePath === `/chat/${session.id}`
                    ? "border-[#1f1c18] bg-[#f0e9dc]"
                    : "border-[#e4dccf] bg-white hover:border-[#cbbda6]"
                )}
              >
                <p className="truncate text-sm font-medium">{session.title}</p>
                <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-[#7f776e]">
                  {session.description}
                </p>
              </button>
            ))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <aside
      className={cn(
        "sticky top-0 h-screen shrink-0 overflow-hidden border-r border-[#e4dccf] bg-[#f7f3eb] text-[#1f1c18] transition-[width] duration-300",
        expanded ? "w-[344px]" : "w-[72px]"
      )}
    >
      <div
        className={cn(
          "grid h-full transition-[grid-template-columns] duration-300",
          expanded ? "grid-cols-[72px_minmax(0,1fr)]" : "grid-cols-[72px_0px]"
        )}
      >
        <div className="flex h-full flex-col items-center px-3 py-4">
          <button
            type="button"
            onClick={onOpenDiscover}
            className="flex h-11 w-11 items-center justify-center rounded-2xl bg-[#1f1c18] text-sm font-semibold text-[#f4efe5]"
          >
            FF
          </button>

          <div className="mt-6 flex flex-col items-center gap-2">
            <RailButton
              active={activePath === "/" || activePath.startsWith("/discover")}
              label="Discover"
              icon={Compass}
              onClick={onOpenDiscover}
            />
            <RailButton
              active={activePath === "/chat/new"}
              label="New chat"
              icon={SquarePen}
              onClick={onOpenNewChat}
            />
            <RailButton
              active={activePath.startsWith("/chat/") && activePath !== "/chat/new"}
              label="History"
              icon={Clock3}
              onClick={onOpenHistory}
            />
          </div>

          <div className="mt-auto flex flex-col items-center gap-3">
            <button
              type="button"
              onClick={() => onExpandedChange(!expanded)}
              className="flex h-10 w-10 items-center justify-center rounded-2xl text-[#6f685f] transition-colors hover:bg-[#ece6dc] hover:text-[#1f1c18]"
              aria-label={expanded ? "Collapse sidebar" : "Expand sidebar"}
              title={expanded ? "Collapse sidebar" : "Expand sidebar"}
            >
              {expanded ? (
                <PanelLeftClose className="h-4.5 w-4.5" />
              ) : (
                <PanelLeft className="h-4.5 w-4.5" />
              )}
            </button>
            <div className="flex h-10 w-10 items-center justify-center rounded-2xl border border-[#d9d1c5] bg-white text-sm font-medium">
              KF
            </div>
          </div>
        </div>

        <div
          className={cn(
            "min-w-0 overflow-hidden border-l border-[#e4dccf]",
            !expanded && "pointer-events-none"
          )}
        >
          <div
            className={cn(
              "flex h-full flex-col px-4 py-4 transition-opacity duration-200",
              expanded ? "opacity-100" : "opacity-0"
            )}
          >
            <div className="border-b border-[#e4dccf] pb-4">
              <p className="font-display text-xl font-semibold">Fashion Feed</p>
              <p className="mt-1 text-sm text-[#7b746a]">Recent threads and workspace</p>
            </div>

            <div className="mt-5 flex items-center gap-2 text-xs uppercase tracking-[0.24em] text-[#8b8479]">
              <Clock3 className="h-3.5 w-3.5" />
              Recent
            </div>

            <div className="mt-3 flex-1 space-y-2 overflow-y-auto pr-1">
              {sessions.map((session) => (
                <button
                  key={session.id}
                  type="button"
                  onClick={() => onSelectSession(session.id)}
                  className={cn(
                    "w-full rounded-2xl border px-3 py-3 text-left transition-colors",
                    activePath === `/chat/${session.id}`
                      ? "border-[#1f1c18] bg-[#f0e9dc]"
                      : "border-[#e4dccf] bg-white hover:border-[#cbbda6]"
                  )}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">{session.title}</p>
                      <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-[#7f776e]">
                        {session.description}
                      </p>
                    </div>
                    <span className="shrink-0 text-[11px] text-[#8a8378]">
                      {formatChinaDateTimeShort(session.updatedAt)}
                    </span>
                  </div>
                </button>
              ))}
            </div>

            <div className="mt-4 rounded-2xl border border-[#e4dccf] bg-white px-3 py-3">
              <p className="text-xs uppercase tracking-[0.18em] text-[#8a8378]">Workspace</p>
              <p className="mt-2 text-sm font-medium">Karl Fashion Feed</p>
              <p className="mt-1 text-xs leading-relaxed text-[#7f776e]">
                Feed-first intelligence workspace with chat and story follow-up.
              </p>
            </div>
          </div>
        </div>
      </div>
    </aside>
  );
}
