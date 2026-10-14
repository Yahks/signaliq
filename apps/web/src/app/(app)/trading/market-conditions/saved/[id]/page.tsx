"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getErrorMessage, type MarketConditions } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card, CardContent, CardHeader, CardTitle,
} from "@/components/ui/card";

const SECTIONS: Array<{ key: keyof MarketConditions; label: string }> = [
  { key: "what_i_track", label: "What I track" },
  { key: "baselines",    label: "Baselines" },
  { key: "regimes",      label: "Regimes I recognize" },
  { key: "thresholds",   label: "Thresholds" },
  { key: "adjustments",  label: "Process adjustments" },
  { key: "mechanisms",   label: "Mechanisms I understand" },
];

export default function SavedConditionsDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const qc = useQueryClient();

  const q = useQuery({
    queryKey: ["market-conditions", params.id],
    queryFn: () => api.marketConditions.getById(params.id),
  });

  const remove = useMutation({
    mutationFn: () => api.marketConditions.remove(params.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["market-conditions-list"] });
      router.push("/trading/market-conditions/saved");
    },
  });

  if (q.isLoading) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }
  if (!q.data) {
    return (
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Saved version not found.
        </p>
        <Link href="/trading/market-conditions/saved">
          <Button variant="outline">Back to saved list</Button>
        </Link>
      </div>
    );
  }

  const doc = q.data;
  const filled = SECTIONS.filter(
    (s) => (doc[s.key] || "").trim().length > 0,
  ).length;

  const onDelete = () => {
    if (
      window.confirm(
        "Move this saved version to trash? You can write a new one anytime.",
      )
    ) {
      remove.mutate();
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <p className="text-xs text-muted-foreground">
            <Link
              href="/trading/market-conditions/saved"
              className="hover:underline"
            >
              ← All saved versions
            </Link>
          </p>
          <h1 className="text-2xl font-semibold mt-2">Saved market conditions</h1>
          <p className="text-sm text-muted-foreground">
            Read-only. {filled} of {SECTIONS.length} sections filled.{" "}
            {doc.updated_at
              ? `Saved ${new Date(doc.updated_at).toLocaleString()}.`
              : ""}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            onClick={onDelete}
            disabled={remove.isPending}
            className="text-red-400 border-red-500/30 hover:bg-red-500/10"
          >
            {remove.isPending ? "Moving…" : "Move to trash"}
          </Button>
          <Link href="/trading/market-conditions">
            <Button>Compose new</Button>
          </Link>
        </div>
      </div>

      {remove.isError && (
        <p className="text-sm text-red-400 border border-red-500/30 rounded-md px-3 py-2 bg-red-500/5">
          {getErrorMessage(remove.error, "Delete failed")}
        </p>
      )}

      {SECTIONS.map((s) => {
        const value = (doc[s.key] as string) || "";
        const isEmpty = value.trim().length === 0;
        return (
          <Card key={s.key}>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">{s.label}</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm whitespace-pre-wrap leading-relaxed">
                {isEmpty ? (
                  <span className="text-muted-foreground italic">(empty)</span>
                ) : (
                  value
                )}
              </p>
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
