import { useMemo, useState } from "react";
import { Compass, LogOut, Search, SquarePen } from "lucide-react";
import type { ChatSession } from "@/lib/chat";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { formatChinaDateTimeShort } from "@/lib/time";

interface AppSidebarProps {
  expanded: boolean;
  mobile?: boolean;
  sessions: ChatSession[];
  currentUserLabel: string;
  activePath: string;
  onExpandedChange: (expanded: boolean) => void;
  onOpenDiscover: () => void;
  onOpenNewChat: () => void;
  onOpenHistory: () => void;
  onSelectSession: (sessionId: string) => void;
  onLogout: () => void;
}

function getInitials(label: string) {
  const parts = label.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) {
    return "KF";
  }

  return parts
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
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
  currentUserLabel,
  activePath,
  onExpandedChange,
  onOpenDiscover,
  onOpenNewChat,
  onOpenHistory,
  onSelectSession,
  onLogout,
}: AppSidebarProps) {
  const [historyQuery, setHistoryQuery] = useState("");
  const userInitials = getInitials(currentUserLabel);
  const filteredSessions = useMemo(() => {
    const query = historyQuery.trim().toLowerCase();
    if (!query) {
      return sessions;
    }

    return sessions.filter((session) => {
      const haystack = `${session.title} ${session.description}`.toLowerCase();
      return haystack.includes(query);
    });
  }, [historyQuery, sessions]);

  if (mobile) {
    return (
      <div className="flex h-full flex-col bg-[#f7f3eb] text-[#1f1c18]">
        <div className="border-b border-[#e4dccf] px-4 py-4 pr-14">
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

        <div className="border-b border-[#e4dccf] px-4 py-4">
          <div className="flex flex-col gap-1">
            <Button
              variant="ghost"
              className={cn(
                "ff-motion-soft w-full justify-start rounded-2xl px-3 text-[#6f685f] hover:bg-[#ece6dc] hover:text-[#1f1c18]",
                (activePath === "/" || activePath.startsWith("/discover")) &&
                  "bg-[#ece6dc] text-[#1f1c18]"
              )}
              onClick={onOpenDiscover}
            >
              <Compass className="h-4 w-4" />
              Discover
            </Button>
            <Button
              variant="ghost"
              className={cn(
                "ff-motion-soft w-full justify-start rounded-2xl px-3 text-[#6f685f] hover:bg-[#ece6dc] hover:text-[#1f1c18]",
                activePath === "/chat/new" && "bg-[#ece6dc] text-[#1f1c18]"
              )}
              onClick={onOpenNewChat}
            >
              <SquarePen className="h-4 w-4" />
              New chat
            </Button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-4 py-4">
          <div className="flex items-center gap-2 px-1 text-xs uppercase tracking-[0.24em] text-[#8b8479]">
            <Search className="h-3.5 w-3.5" />
            History
          </div>

          <div className="mt-3">
            <Input
              value={historyQuery}
              onChange={(event) => setHistoryQuery(event.target.value)}
              placeholder="Search history..."
              className="ff-motion-soft h-10 rounded-2xl border-[#ddd4c7] bg-white px-4 shadow-none"
            />
          </div>

          <div className="mt-4 space-y-2">
            {filteredSessions.map((session) => (
              <button
                key={session.id}
                type="button"
                onClick={() => onSelectSession(session.id)}
                className={cn(
                  "ff-motion-soft w-full rounded-2xl px-3 py-3 text-left",
                  activePath === `/chat/${session.id}`
                    ? "bg-[#f0e9dc]"
                    : "bg-white hover:bg-[#efe8dc]"
                )}
              >
                <p className="truncate text-sm font-medium">{session.title}</p>
                <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-[#7f776e]">
                  {session.description}
                </p>
              </button>
            ))}
            {filteredSessions.length === 0 && (
              <div className="rounded-2xl bg-white px-3 py-4 text-sm text-[#7f776e]">
                No matching history.
              </div>
            )}
          </div>
        </div>

        <div className="border-t border-[#e4dccf] px-4 py-4">
          <div className="flex items-center justify-between">
            <div className="flex h-10 w-10 items-center justify-center rounded-2xl border border-[#d9d1c5] bg-[#f7f3eb] text-sm font-medium text-[#1f1c18]">
              {userInitials}
            </div>
            <Button
              variant="ghost"
              size="icon-sm"
              className="rounded-full text-[#6f685f] hover:bg-[#ece6dc] hover:text-[#1f1c18]"
              onClick={onLogout}
              aria-label="Logout"
            >
              <LogOut className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <aside
      data-expanded={expanded ? "true" : "false"}
      className={cn(
        "ff-motion-sidebar sticky top-0 h-screen shrink-0 overflow-hidden border-r border-[#e4dccf] bg-[#f7f3eb] text-[#1f1c18]",
        expanded ? "w-[288px]" : "w-[72px]"
      )}
    >
      <div className={cn("flex h-full flex-col px-3 py-4", expanded ? "items-stretch" : "items-center")}>
        <div className={cn("flex items-center", expanded ? "justify-between gap-3" : "justify-center")}>
          <button
            type="button"
            onClick={() => onExpandedChange(!expanded)}
            className={cn(
              "flex h-11 w-11 items-center justify-center rounded-2xl text-sm font-semibold transition-colors",
              expanded
                ? "bg-[#1f1c18] text-[#f4efe5]"
                : "bg-[#1f1c18] text-[#f4efe5] hover:bg-[#2c2721]"
            )}
            aria-label={expanded ? "Collapse sidebar" : "Expand sidebar"}
            title={expanded ? "Collapse sidebar" : "Expand sidebar"}
          >
            FF
          </button>
          <div
            className={cn(
              "ff-sidebar-reveal min-w-0 flex-1 overflow-hidden",
              expanded
                ? "pointer-events-auto max-w-[180px] opacity-100"
                : "pointer-events-none max-w-0 opacity-0"
            )}
          >
            <p className="truncate font-display text-lg font-semibold">Fashion Feed</p>
            <p className="truncate text-xs text-[#7b746a]">Recent threads and workspace</p>
          </div>
        </div>

        <div className={cn("mt-6 flex", expanded ? "flex-col gap-1" : "flex-col items-center gap-2")}>
          {expanded ? (
            <>
              <Button
                variant="ghost"
                className={cn(
                  "ff-motion-soft w-full justify-start rounded-2xl px-3 text-[#6f685f] hover:bg-[#ece6dc] hover:text-[#1f1c18]",
                  (activePath === "/" || activePath.startsWith("/discover")) &&
                    "bg-[#ece6dc] text-[#1f1c18]"
                )}
                onClick={onOpenDiscover}
              >
                <Compass className="h-4 w-4" />
                Discover
              </Button>
              <Button
                variant="ghost"
                className={cn(
                  "ff-motion-soft w-full justify-start rounded-2xl px-3 text-[#6f685f] hover:bg-[#ece6dc] hover:text-[#1f1c18]",
                  activePath === "/chat/new" && "bg-[#ece6dc] text-[#1f1c18]"
                )}
                onClick={onOpenNewChat}
              >
                <SquarePen className="h-4 w-4" />
                New chat
              </Button>
            </>
          ) : (
            <>
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
                label="Search history"
                icon={Search}
                onClick={onOpenHistory}
              />
            </>
          )}
        </div>

        <div
          className={cn(
            "ff-sidebar-reveal min-h-0 overflow-hidden",
            expanded
              ? "pointer-events-auto mt-6 flex-1 opacity-100"
              : "pointer-events-none mt-0 max-h-0 flex-none opacity-0"
          )}
        >
          <div className="flex items-center gap-2 px-1 text-xs uppercase tracking-[0.24em] text-[#8b8479]">
            <Search className="h-3.5 w-3.5" />
            History
          </div>

          <div className="mt-3">
            <Input
              value={historyQuery}
              onChange={(event) => setHistoryQuery(event.target.value)}
              placeholder="Search history..."
              className="ff-motion-soft h-10 rounded-2xl border-[#ddd4c7] bg-white px-4 shadow-none"
            />
          </div>

          <div className="mt-3 flex h-full min-h-0 flex-1 flex-col overflow-hidden pr-1">
            <div className="flex-1 min-h-0 space-y-2 overflow-y-auto">
              {filteredSessions.map((session) => (
                <button
                  key={session.id}
                  type="button"
                  onClick={() => onSelectSession(session.id)}
                  className={cn(
                    "ff-motion-soft w-full rounded-2xl px-3 py-3 text-left",
                    activePath === `/chat/${session.id}`
                      ? "bg-[#f0e9dc]"
                      : "hover:bg-[#efe8dc]"
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
              {filteredSessions.length === 0 && (
                <div className="rounded-2xl bg-white px-3 py-4 text-sm text-[#7f776e]">
                  No matching history.
                </div>
              )}
            </div>
          </div>

          <div className="mt-4 rounded-2xl bg-white px-3 py-3">
            <p className="text-xs uppercase tracking-[0.18em] text-[#8a8378]">Workspace</p>
            <p className="mt-2 text-sm font-medium">Karl Fashion Feed</p>
            <p className="mt-1 text-xs leading-relaxed text-[#7f776e]">
              Feed-first intelligence workspace with chat and story follow-up.
            </p>
          </div>
        </div>

        <div className="mt-auto pt-4">
          <div
            className={cn(
              "flex items-center",
              expanded
                ? "justify-between gap-3 px-1 py-2"
                : "flex-col gap-2 px-0 py-2"
            )}
          >
            <div className="flex h-10 w-10 items-center justify-center rounded-2xl border border-[#d9d1c5] bg-[#f7f3eb] text-sm font-medium">
              {userInitials}
            </div>
            <Button
              variant="ghost"
              size="icon-sm"
              className="rounded-full text-[#6f685f] hover:bg-[#ece6dc] hover:text-[#1f1c18]"
              onClick={onLogout}
              aria-label="Logout"
              title="Logout"
            >
              <LogOut className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </div>
    </aside>
  );
}
