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
        "flex h-11 w-11 items-center justify-center rounded-[20px] border transition-colors shadow-[0_10px_24px_rgba(44,33,16,0.03)]",
        active
          ? "border-[#ceb083] bg-[#f2e7d6] text-[#2b241d]"
          : "border-[#e2d8cb] bg-[rgba(255,255,255,0.82)] text-[#6f685f] hover:border-[#c8b18a] hover:text-[#1f1c18]"
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
            <div className="flex h-10 w-10 items-center justify-center rounded-[20px] border border-[#ceb083] bg-[#f2e7d6] text-sm font-semibold text-[#2b241d]">
              FF
            </div>
            <div>
              <p className="font-display text-xl font-semibold">Fashion Feed</p>
              <p className="text-xs text-[#7c756b]">资讯总览 / 对话工作台</p>
            </div>
          </div>
        </div>

        <div className="border-b border-[#e4dccf] px-4 py-4">
          <div className="flex flex-col gap-1">
            <Button
              variant="ghost"
              className={cn(
                "ff-motion-soft h-11 w-full justify-start rounded-[18px] px-4 text-[#6f685f] hover:bg-[#f3ebde] hover:text-[#1f1c18]",
                (activePath === "/" || activePath.startsWith("/discover")) &&
                  "bg-[#f2e7d6] text-[#2b241d]"
              )}
              onClick={onOpenDiscover}
            >
              <Compass className="h-4 w-4" />
              资讯总览
            </Button>
            <Button
              variant="ghost"
              className={cn(
                "ff-motion-soft h-11 w-full justify-start rounded-[18px] px-4 text-[#6f685f] hover:bg-[#f3ebde] hover:text-[#1f1c18]",
                activePath === "/chat/new" && "bg-[#f2e7d6] text-[#2b241d]"
              )}
              onClick={onOpenNewChat}
            >
              <SquarePen className="h-4 w-4" />
              新建对话
            </Button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-4 py-4">
          <div className="relative">
            <Search className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-[#9a9288]" />
            <Input
              value={historyQuery}
              onChange={(event) => setHistoryQuery(event.target.value)}
              placeholder="搜索历史对话..."
              className="ff-motion-soft h-11 rounded-[22px] border-[#ddd4c7] bg-[rgba(255,255,255,0.92)] pl-11 pr-4 shadow-[0_10px_24px_rgba(44,33,16,0.03)]"
            />
          </div>

          <div className="mt-4 space-y-2">
            {filteredSessions.map((session) => (
              <button
                key={session.id}
                type="button"
                onClick={() => onSelectSession(session.id)}
                className={cn(
                  "ff-motion-soft w-full rounded-[18px] px-3 py-2.5 text-left",
                  activePath === `/chat/${session.id}`
                    ? "bg-[#f2e7d6] text-[#2b241d]"
                    : "text-[#5f584f] hover:bg-[#f8f2e8]"
                )}
              >
                <div className="flex items-center justify-between gap-3">
                  <p className="truncate text-sm font-medium">{session.title}</p>
                  <span className="shrink-0 text-[11px] text-[#8a8378]">
                    {formatChinaDateTimeShort(session.updatedAt)}
                  </span>
                </div>
              </button>
            ))}
            {filteredSessions.length === 0 && (
              <div className="px-3 py-4 text-sm text-[#7f776e]">
                没有匹配的历史对话。
              </div>
            )}
          </div>
        </div>

        <div className="border-t border-[#e4dccf] px-4 py-4">
          <div className="flex items-center justify-between">
            <div className="flex h-10 w-10 items-center justify-center rounded-[20px] border border-[#d9d1c5] bg-[#f7f3eb] text-sm font-medium text-[#1f1c18]">
              {userInitials}
            </div>
            <Button
              variant="ghost"
              size="icon-sm"
              className="rounded-full text-[#6f685f] hover:bg-[#ece6dc] hover:text-[#1f1c18]"
              onClick={onLogout}
              aria-label="退出登录"
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
              "flex h-11 w-11 items-center justify-center rounded-[20px] border text-sm font-semibold transition-colors",
              expanded
                ? "border-[#ceb083] bg-[#f2e7d6] text-[#2b241d]"
                : "border-[#ceb083] bg-[#f2e7d6] text-[#2b241d] hover:bg-[#ecdfc8]"
            )}
            aria-label={expanded ? "收起侧边栏" : "展开侧边栏"}
            title={expanded ? "收起侧边栏" : "展开侧边栏"}
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
            <p className="truncate text-xs text-[#7b746a]">资讯总览与对话工作台</p>
          </div>
        </div>

        <div className={cn("mt-6 flex", expanded ? "flex-col gap-1" : "flex-col items-center gap-2")}>
          {expanded ? (
            <>
              <Button
              variant="ghost"
              className={cn(
                "ff-motion-soft h-11 w-full justify-start rounded-[18px] px-4 text-[#6f685f] hover:bg-[#f3ebde] hover:text-[#1f1c18]",
                (activePath === "/" || activePath.startsWith("/discover")) &&
                    "bg-[#f2e7d6] text-[#2b241d]"
              )}
              onClick={onOpenDiscover}
            >
                <Compass className="h-4 w-4" />
                资讯总览
              </Button>
              <Button
              variant="ghost"
              className={cn(
                "ff-motion-soft h-11 w-full justify-start rounded-[18px] px-4 text-[#6f685f] hover:bg-[#f3ebde] hover:text-[#1f1c18]",
                activePath === "/chat/new" && "bg-[#f2e7d6] text-[#2b241d]"
              )}
              onClick={onOpenNewChat}
            >
                <SquarePen className="h-4 w-4" />
                新建对话
              </Button>
            </>
          ) : (
            <>
              <RailButton
                active={activePath === "/" || activePath.startsWith("/discover")}
                label="资讯总览"
                icon={Compass}
                onClick={onOpenDiscover}
              />
              <RailButton
                active={activePath === "/chat/new"}
                label="新建对话"
                icon={SquarePen}
                onClick={onOpenNewChat}
              />
              <RailButton
                active={activePath.startsWith("/chat/") && activePath !== "/chat/new"}
                label="历史记录"
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
          <div className="relative">
            <Search className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-[#9a9288]" />
            <Input
              value={historyQuery}
              onChange={(event) => setHistoryQuery(event.target.value)}
              placeholder="搜索历史对话..."
              className="ff-motion-soft h-11 rounded-[22px] border-[#ddd4c7] bg-[rgba(255,255,255,0.92)] pl-11 pr-4 shadow-[0_10px_24px_rgba(44,33,16,0.03)]"
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
                    "ff-motion-soft w-full rounded-[18px] px-3 py-2.5 text-left",
                    activePath === `/chat/${session.id}`
                      ? "bg-[#f2e7d6] text-[#2b241d]"
                      : "text-[#5f584f] hover:bg-[#f8f2e8]"
                  )}
                >
                  <div className="flex items-start justify-between gap-3">
                    <p className="min-w-0 truncate text-sm font-medium">{session.title}</p>
                    <span className="shrink-0 text-[11px] text-[#8a8378]">
                      {formatChinaDateTimeShort(session.updatedAt)}
                    </span>
                  </div>
                </button>
              ))}
              {filteredSessions.length === 0 && (
                <div className="px-3 py-4 text-sm text-[#7f776e]">
                  没有匹配的历史对话。
                </div>
              )}
            </div>
          </div>

          <div className="mt-4 rounded-[24px] border border-[#e2d8cb] bg-[rgba(255,255,255,0.72)] px-4 py-4 shadow-[0_12px_28px_rgba(44,33,16,0.03)]">
            <p className="text-xs uppercase tracking-[0.18em] text-[#8a8378]">工作区</p>
            <p className="mt-2 text-sm font-medium">Karl Fashion Feed</p>
            <p className="mt-1 text-xs leading-relaxed text-[#7f776e]">
              围绕资讯总览、专题详情与对话追问的一体化工作台。
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
              aria-label="退出登录"
              title="退出登录"
            >
              <LogOut className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </div>
    </aside>
  );
}
