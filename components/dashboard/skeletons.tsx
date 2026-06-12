import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

/** Generic page-level loading state shown before client data mounts. */
export function PageSkeleton() {
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <Skeleton className="h-6 w-48" />
        <Skeleton className="h-4 w-80" />
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <Card key={i} className="p-5">
            <Skeleton className="h-4 w-28" />
            <Skeleton className="mt-3 h-7 w-24" />
            <Skeleton className="mt-3 h-3 w-32" />
          </Card>
        ))}
      </div>
      <Card className="p-6">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="mt-4 h-48 w-full" />
      </Card>
      <Card className="p-6">
        <Skeleton className="h-4 w-40" />
        <div className="mt-4 space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      </Card>
    </div>
  );
}
