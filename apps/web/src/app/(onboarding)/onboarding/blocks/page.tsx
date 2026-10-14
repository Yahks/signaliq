"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { api, getErrorMessage, type BlockOption } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { cn } from "@/lib/utils";

const MAX = 2;

export default function OnboardingBlocksPage() {
  const router = useRouter();
  const [selected, setSelected] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { data, isLoading, error: loadErr } = useQuery({
    queryKey: ["onboarding-state"],
    queryFn: () => api.getOnboardingState(),
  });

  // If the user has not acknowledged the guide yet, send them back.
  useEffect(() => {
    if (data && !data.guide_seen) {
      router.replace("/onboarding/guide");
    }
  }, [data, router]);

  function toggle(slug: string) {
    setSelected((cur) => {
      if (cur.includes(slug)) return cur.filter((s) => s !== slug);
      if (cur.length >= MAX) return cur;
      return [...cur, slug];
    });
  }

  async function onConfirm() {
    if (selected.length < 1) return;
    setError(null);
    setLoading(true);
    try {
      await api.selectBlocks(selected);
      // S.7.5a — mark onboarded so the middleware lets the dashboard through.
      const { setOnboardedCookie } = await import("@/lib/cookies");
      setOnboardedCookie(true);
      router.push("/dashboard");
    } catch (err) {
      setError(getErrorMessage(err, "Could not save your selection"));
    } finally {
      setLoading(false);
    }
  }

  if (isLoading) {
    return (
      <Card>
        <CardContent className="py-8 text-center text-sm text-muted-foreground">
          Loading services…
        </CardContent>
      </Card>
    );
  }

  if (loadErr || !data) {
    return (
      <Card>
        <CardContent className="space-y-4 py-8 text-center text-sm">
          <p className="text-destructive">
            {getErrorMessage(loadErr, "Could not load services")}
          </p>
          <Button variant="outline" onClick={() => router.refresh()}>
            Try again
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Choose your services</CardTitle>
        <CardDescription>
          Pick one or two. You can change these later in Settings.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          {data.available_blocks.map((b: BlockOption) => {
            const isSelected = selected.includes(b.slug);
            const isDisabled = !isSelected && selected.length >= MAX;
            return (
              <button
                key={b.slug}
                type="button"
                disabled={isDisabled}
                onClick={() => toggle(b.slug)}
                className={cn(
                  "rounded-md border p-4 text-left transition",
                  isSelected
                    ? "border-primary bg-primary/5"
                    : "border-border hover:border-primary/50",
                  isDisabled && "cursor-not-allowed opacity-50",
                )}
              >
                <div className="mb-1 flex items-center justify-between">
                  <span className="font-medium">{b.name}</span>
                  {isSelected && (
                    <span className="text-xs text-primary">Selected</span>
                  )}
                </div>
                {b.description && (
                  <p className="text-xs text-muted-foreground">
                    {b.description}
                  </p>
                )}
              </button>
            );
          })}
        </div>

        <p className="text-xs text-muted-foreground">
          {selected.length}/{MAX} selected
          {selected.length >= MAX && " — deselect one to change"}
        </p>

        {error && <p className="text-sm text-destructive">{error}</p>}

        <Button
          type="button"
          className="w-full"
          disabled={selected.length < 1 || loading}
          onClick={onConfirm}
        >
          {loading ? "Saving…" : "Confirm selection"}
        </Button>
      </CardContent>
    </Card>
  );
}
