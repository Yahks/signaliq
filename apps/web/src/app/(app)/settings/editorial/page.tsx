"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getErrorMessage } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

// Human-readable labels for the cards. The `directive` from the API
// is the literal instruction the model receives; these are the names
// a user sees when choosing.
const VOICE_LABELS: Record<string, { title: string; blurb: string }> = {
  wire: {
    title: "The Wire",
    blurb:
      "Data-forward newswire. Reports the numbers, names the rotation, states one implication. No narrative.",
  },
  analyst: {
    title: "The Analyst",
    blurb:
      "Sell-side voice. Interprets the rotation in risk/reward terms. Cites every number. Half the piece on what happened, half on what it means.",
  },
  contrarian: {
    title: "The Contrarian",
    blurb:
      "States the consensus reading, then points at the tension the consensus is missing. Ends with what to watch.",
  },
  retail: {
    title: "The Retail Advisor",
    blurb:
      "Plain language for a mixed growth-and-defensive portfolio. Defines jargon in one phrase. No advice.",
  },
  academic: {
    title: "The Academic",
    blurb:
      "Structural, hedged. Places the move in a regime framework. Cautious language. Locates rather than predicts.",
  },
};

export default function EditorialPage() {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const settings = useQuery({
    queryKey: ["editorial-settings"],
    queryFn: () => api.getEditorialSettings(),
  });

  useEffect(() => {
    if (settings.data && selected === null) {
      setSelected(settings.data.voice);
    }
  }, [settings.data, selected]);

  const save = useMutation({
    mutationFn: (voice: string) => api.setEditorialVoice(voice),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["editorial-settings"] });
      setSaved(true);
      setError(null);
      setTimeout(() => setSaved(false), 3000);
    },
    onError: (e) => setError(getErrorMessage(e, "Failed to save")),
  });

  if (settings.isLoading) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }
  if (!settings.data) {
    return <p className="text-sm text-destructive">Failed to load settings.</p>;
  }

  const current = settings.data.voice;
  const dirty = selected !== null && selected !== current;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Trading voice</h1>
        <p className="text-sm text-muted-foreground">
          The event is shared. The voice is yours. Every trading post
          this organisation generates is written in this editorial voice.
        </p>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}
      {saved && <p className="text-sm text-green-500">Saved.</p>}

      <div className="grid gap-4 md:grid-cols-2">
        {settings.data.available.map((v) => {
          const label = VOICE_LABELS[v.name] ?? { title: v.name, blurb: "" };
          const isSelected = selected === v.name;
          const isCurrent = current === v.name;
          return (
            <Card
              key={v.name}
              onClick={() => setSelected(v.name)}
              className={
                "cursor-pointer transition " +
                (isSelected
                  ? "border-primary ring-1 ring-primary"
                  : "hover:border-muted-foreground/40")
              }
            >
              <CardHeader>
                <div className="flex items-start justify-between gap-3">
                  <CardTitle className="flex items-center gap-2">
                    {label.title}
                    {isCurrent && (
                      <span className="rounded-md bg-primary/15 px-2 py-0.5 text-xs text-primary">
                        current
                      </span>
                    )}
                  </CardTitle>
                  <span
                    aria-hidden
                    className={
                      "mt-1 h-4 w-4 shrink-0 rounded-full border " +
                      (isSelected
                        ? "border-primary bg-primary"
                        : "border-muted-foreground/40")
                    }
                  />
                </div>
                <CardDescription>{label.blurb}</CardDescription>
              </CardHeader>
              <CardContent>
                <details className="text-xs text-muted-foreground">
                  <summary className="cursor-pointer">
                    Show the model&apos;s instructions
                  </summary>
                  <pre className="mt-2 whitespace-pre-wrap font-mono text-[11px] leading-relaxed">
                    {v.directive}
                  </pre>
                </details>
              </CardContent>
            </Card>
          );
        })}
      </div>

      <div className="flex gap-3">
        <Button
          onClick={() => selected && save.mutate(selected)}
          disabled={!dirty || save.isPending}
        >
          {save.isPending ? "Saving…" : dirty ? "Save voice" : "No changes"}
        </Button>
        {dirty && (
          <Button variant="ghost" onClick={() => setSelected(current)}>
            Cancel
          </Button>
        )}
      </div>
    </div>
  );
}
