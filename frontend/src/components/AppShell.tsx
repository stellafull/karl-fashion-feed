import { useMemo, useState } from "react";
import { AlertCircle, Menu } from "lucide-react";
import { Route, Switch, useLocation } from "wouter";
import AppSidebar from "@/components/AppSidebar";
import LoginScreen from "@/components/LoginScreen";
import LoadingSkeleton from "@/components/LoadingSkeleton";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/hooks/useAuth";
import { useFeedData } from "@/hooks/useFeedData";
import { useChatSessions } from "@/hooks/useChatSessions";
import type { ChatUploadAttachment, StoryChatContext } from "@/lib/chat";
import DiscoverPage from "@/pages/DiscoverPage";
import ChatPage from "@/pages/ChatPage";
import StoryPage from "@/pages/StoryPage";
import NotFound from "@/pages/NotFound";

export default function AppShell() {
  const [location, setLocation] = useLocation();
  const [sidebarExpanded, setSidebarExpanded] = useState(() => location.startsWith("/chat"));
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const {
    user,
    hydrated: authHydrated,
    isAuthenticated,
    isSubmitting: authSubmitting,
    error: authError,
    login,
    logout,
  } = useAuth();
  const {
    data,
    loading,
    error,
    activeCategory,
    setActiveCategory,
    featuredTopic,
    gridTopics,
    sortMode,
    setSortMode,
    selectedSources,
    toggleSource,
    clearSourceFilter,
    availableSources,
    filteredTopics,
  } = useFeedData();
  const topics = data?.topics ?? [];
  const {
    hydrated,
    error: chatError,
    sessions,
    createSession,
    createDeepResearchSession,
    createStorySession,
    createStoryDeepResearchSession,
    sendMessage,
    sendDeepResearchMessage,
    canInterruptMessage,
    interruptMessage,
  } = useChatSessions();

  const currentTitle = useMemo(() => {
    if (location.startsWith("/chat")) {
      return "Chat";
    }
    if (location.startsWith("/stories/")) {
      return "Story";
    }
    return "Discover";
  }, [location]);

  const createSessionWithAttachments = async (
    question: string,
    attachments: ChatUploadAttachment[] = []
  ) => createSession(question, attachments);

  const sendMessageWithAttachments = async (
    sessionId: string,
    question: string,
    attachments: ChatUploadAttachment[] = []
  ) => sendMessage(sessionId, question, attachments);

  const createStorySessionWithAttachments = async (
    question: string,
    attachments: ChatUploadAttachment[] = [],
    storyContext?: StoryChatContext
  ) => createStorySession(question, attachments, storyContext);

  const createStoryDeepResearchSessionWithAttachments = async (
    question: string,
    attachments: ChatUploadAttachment[] = [],
    storyContext?: StoryChatContext
  ) => createStoryDeepResearchSession(question, attachments, storyContext);

  const openDiscover = () => {
    setLocation("/discover");
    setMobileSidebarOpen(false);
  };

  const openNewChat = () => {
    setLocation("/chat/new");
    setMobileSidebarOpen(false);
  };

  const openHistory = () => {
    setSidebarExpanded(true);
    setMobileSidebarOpen(false);
  };

  const selectSession = (sessionId: string) => {
    setSidebarExpanded(true);
    setLocation(`/chat/${sessionId}`);
    setMobileSidebarOpen(false);
  };

  if (!authHydrated) {
    return <LoadingSkeleton />;
  }

  if (!isAuthenticated) {
    return (
      <LoginScreen
        isSubmitting={authSubmitting}
        error={authError}
        onSubmit={login}
      />
    );
  }

  if (loading || !hydrated) {
    return <LoadingSkeleton />;
  }

  if (error || chatError || !data) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#f7f3eb] px-6">
        <div className="max-w-md space-y-4 text-center">
          <AlertCircle className="mx-auto h-12 w-12 text-[#9f7d45]" />
          <h1 className="font-display text-4xl text-[#2b241d]">无法加载资讯</h1>
          <p className="text-base leading-7 text-[#665f56]">
            {error || chatError || "数据加载失败，请稍后刷新页面重试。"}
          </p>
          <Button onClick={() => window.location.reload()}>刷新页面</Button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#f7f3eb] text-[#1f1c18] md:flex md:h-screen md:overflow-hidden">
      <div className="hidden shrink-0 md:block">
        <AppSidebar
          expanded={sidebarExpanded}
          sessions={sessions}
          currentUserLabel={user?.display_name || user?.login_name || "Local User"}
          activePath={location}
          onExpandedChange={setSidebarExpanded}
          onOpenDiscover={openDiscover}
          onOpenNewChat={openNewChat}
          onOpenHistory={openHistory}
          onSelectSession={selectSession}
          onLogout={logout}
        />
      </div>

      <Sheet open={mobileSidebarOpen} onOpenChange={setMobileSidebarOpen}>
        <SheetContent side="left" className="w-[24rem] p-0">
          <AppSidebar
            mobile
            expanded
            sessions={sessions}
            currentUserLabel={user?.display_name || user?.login_name || "Local User"}
            activePath={location}
            onExpandedChange={setSidebarExpanded}
            onOpenDiscover={openDiscover}
            onOpenNewChat={openNewChat}
            onOpenHistory={openHistory}
            onSelectSession={selectSession}
            onLogout={logout}
          />
        </SheetContent>
      </Sheet>

      <div className="h-screen min-w-0 flex-1 overflow-hidden md:min-h-0">
        <div className="sticky top-0 z-40 flex items-center justify-between border-b border-[#e4dccf] bg-[#f7f3eb]/95 px-4 py-3 backdrop-blur md:hidden">
          <button
            type="button"
            onClick={() => setMobileSidebarOpen(true)}
            className="flex h-10 w-10 items-center justify-center rounded-2xl border border-[#ddd4c7] bg-white text-[#1f1c18]"
            aria-label="Open navigation"
          >
            <Menu className="h-4 w-4" />
          </button>
          <div className="text-sm font-medium text-[#6b6358]">{currentTitle}</div>
          <div className="h-10 w-10" />
        </div>

        <div className="h-[calc(100dvh-65px)] md:h-full md:min-h-0">
          <Switch>
            <Route path="/discover">
              <DiscoverPage
                data={data}
                activeCategory={activeCategory}
                sortMode={sortMode}
                featuredTopic={featuredTopic}
                gridTopics={gridTopics}
                filteredTopics={filteredTopics}
                availableSources={availableSources}
                selectedSources={selectedSources}
                onCategoryChange={setActiveCategory}
                onSortChange={setSortMode}
                onToggleSource={toggleSource}
                onClearSources={clearSourceFilter}
                onOpenStory={(storyId) => setLocation(`/stories/${storyId}`)}
              />
            </Route>
            <Route path="/chat/new">
              <ChatPage
                sessions={sessions}
                onCreateSession={createSessionWithAttachments}
                onCreateDeepResearchSession={createDeepResearchSession}
                onSendMessage={sendMessageWithAttachments}
                onSendDeepResearchMessage={sendDeepResearchMessage}
                canInterruptMessage={canInterruptMessage}
                onInterruptMessage={interruptMessage}
              />
            </Route>
            <Route path="/chat/:sessionId">
              {(params) => (
                <ChatPage
                  sessionId={params.sessionId}
                  sessions={sessions}
                  onCreateSession={createSessionWithAttachments}
                  onCreateDeepResearchSession={createDeepResearchSession}
                  onSendMessage={sendMessageWithAttachments}
                  onSendDeepResearchMessage={sendDeepResearchMessage}
                  canInterruptMessage={canInterruptMessage}
                  onInterruptMessage={interruptMessage}
                />
              )}
            </Route>
            <Route path="/stories/:storyId">
              {(params) => (
                <StoryPage
                  storyId={params.storyId}
                  topic={data.topics.find((topic) => topic.id === params.storyId) ?? null}
                  onStartStoryChat={createStorySessionWithAttachments}
                  onStartStoryDeepResearch={createStoryDeepResearchSessionWithAttachments}
                />
              )}
            </Route>
            <Route path="/">
              <DiscoverPage
                data={data}
                activeCategory={activeCategory}
                sortMode={sortMode}
                featuredTopic={featuredTopic}
                gridTopics={gridTopics}
                filteredTopics={filteredTopics}
                availableSources={availableSources}
                selectedSources={selectedSources}
                onCategoryChange={setActiveCategory}
                onSortChange={setSortMode}
                onToggleSource={toggleSource}
                onClearSources={clearSourceFilter}
                onOpenStory={(storyId) => setLocation(`/stories/${storyId}`)}
              />
            </Route>
            <Route>
              <NotFound />
            </Route>
          </Switch>
        </div>
      </div>
    </div>
  );
}
