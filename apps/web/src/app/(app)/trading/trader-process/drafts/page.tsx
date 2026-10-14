"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

export default function DraftsPage() {
  const q = useQuery({
    queryKey: ["trader-process-drafts"],
    queryFn: async () => {
      const [pending, accepted] = await Promise.all([
        api.traderProcess.listDrafts("pending"),
        api.traderProcess.listDrafts("accepted"),
      ]);
      return [...pending, ...accepted].sort(
        (a, b) =>
          new Date(b.created_at).getTime() -
          new Date(a.created_at).getTime(),
      );
    },
  });

  const drafts = q.data ?? [];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Model drafts</h1>
        <p className="text-sm text-muted-foreground">
          {drafts.length} {drafts.length === 1 ? "draft" : "drafts"} to review.
          Each is a complete nine-section process document the model wrote.
          Open one, edit it, save it as your process.
        </p>
      </div>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">What these are</CardTitle>
          <CardDescription>
            The model writes a draft of your process every 6 hours. These
            drafts never appear in published articles — only your saved
            process does. Review one, edit the specifics, save as your
            process.
          </CardDescription>
        </CardHeader>
      </Card>

      {q.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : drafts.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No drafts to review. A new draft appears here every 6 hours.
        </p>
      ) : (
        <div className="space-y-2">
          {drafts.map((d) => (
            <Link
              key={d.id}
              href={`/trading/trader-process/drafts/${d.id}`}
              className="block"
            >
              <Card className="hover:bg-muted/30 transition-colors">
                <CardContent className="py-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <p className="text-sm font-medium">
                          Process draft
                        </p>
                        {d.reviewed_at ? (
                          <span className="text-[10px] uppercase tracking-wider text-green-400 border border-green-500/30 rounded px-1">
                            accepted
                          </span>
                        ) : (
                          <span className="text-[10px] uppercase tracking-wider text-amber-400 border border-amber-500/30 rounded px-1">
                            draft
                          </span>
                        )}
                      </div>
                      <p className="text-sm text-muted-foreground mt-1 line-clamp-2">
                        {d.preview || "(empty preview)"}
                      </p>
                    </div>
                    <p className="text-[10px] text-muted-foreground whitespace-nowrap">
                      {fmtDate(d.created_at)}
                    </p>
                  </div>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
