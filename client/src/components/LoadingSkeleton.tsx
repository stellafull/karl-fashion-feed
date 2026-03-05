/*
 * LoadingSkeleton — Editorial Noir Design
 * Elegant loading state with pulsing placeholders
 */

export default function LoadingSkeleton() {
  return (
    <div className="min-h-screen bg-background">
      {/* Header skeleton */}
      <div className="border-b border-border">
        <div className="container flex items-center justify-between h-16">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 bg-muted animate-pulse" />
            <div className="space-y-1.5">
              <div className="w-28 h-5 bg-muted animate-pulse" />
              <div className="w-16 h-2.5 bg-muted animate-pulse" />
            </div>
          </div>
        </div>
      </div>

      {/* Category nav skeleton */}
      <div className="border-b border-border">
        <div className="container flex gap-4 py-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="w-20 h-6 bg-muted animate-pulse" />
          ))}
        </div>
      </div>

      {/* Hero skeleton */}
      <div className="container mt-6">
        <div className="w-full aspect-[21/9] bg-muted animate-pulse" />
      </div>

      {/* Grid skeleton */}
      <div className="container mt-8">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="bg-card border border-border">
              <div className="aspect-[4/3] bg-muted animate-pulse" />
              <div className="p-4 space-y-3">
                <div className="w-16 h-3 bg-muted animate-pulse" />
                <div className="w-full h-5 bg-muted animate-pulse" />
                <div className="w-3/4 h-5 bg-muted animate-pulse" />
                <div className="w-full h-3 bg-muted animate-pulse" />
                <div className="w-2/3 h-3 bg-muted animate-pulse" />
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
