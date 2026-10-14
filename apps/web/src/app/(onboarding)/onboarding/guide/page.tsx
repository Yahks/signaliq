"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api, getErrorMessage } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export default function OnboardingGuidePage() {
  const router = useRouter();
  const [acknowledged, setAcknowledged] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onContinue() {
    if (!acknowledged) return;
    setError(null);
    setLoading(true);
    try {
      await api.acknowledgeOnboarding();
      router.push("/onboarding/blocks");
    } catch (err) {
      setError(getErrorMessage(err, "Could not save your acknowledgement"));
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Welcome to SignalIQ</CardTitle>
        <CardDescription>
          Before you pick your services, here is how the platform works.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6 text-sm leading-relaxed">
        <section>
          <h2 className="mb-2 text-base font-semibold">What SignalIQ is</h2>
          <p className="text-muted-foreground">
            SignalIQ is a multi-service content platform. Each service — a
            &ldquo;block&rdquo; — produces a distinct kind of output from
            sourced data:
          </p>
          <ul className="mt-2 list-disc space-y-1 pl-6 text-muted-foreground">
            <li>
              <strong>Football</strong> — statistical match previews and
              probabilistic predictions, with accuracy tracked publicly.
            </li>
            <li>
              <strong>Trading</strong> — market analysis, fundamental
              snapshots, and educational articles, sourced from public
              market data.
            </li>
            <li>
              <strong>News</strong> — chained analysis of events, each with
              evidence, impacts, and scenarios.
            </li>
          </ul>
          <p className="mt-2 text-muted-foreground">
            Every factual claim is verified against the source data before a
            post is published. Posts that cannot be verified go to your
            draft queue for review, not silently to your feed.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-base font-semibold">
            How you pay — the token model
          </h2>
          <p className="text-muted-foreground">
            You receive <strong>1,000,000 usage tokens</strong> on signup
            (shown as approximately $1.00). Tokens are consumed by the
            content your selected services produce:
          </p>
          <ul className="mt-2 list-disc space-y-1 pl-6 text-muted-foreground">
            <li>Produced content — <strong>0.5 tokens per word</strong>.</li>
            <li>Football predictions — <strong>50 tokens per match</strong>.</li>
            <li>
              Uploads — <strong>1 token per 100 words</strong>.
            </li>
          </ul>
          <p className="mt-2 text-muted-foreground">
            Nothing else is charged. Internal work — reasoning, verification,
            clustering, embeddings — is free to you.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-base font-semibold">
            Your API keys and your tokens are separate
          </h2>
          <p className="text-muted-foreground">
            To generate content you will need to add your own API key from a
            provider such as Groq, Gemini, or OpenRouter. That key pays the
            provider for the language-model call. Your SignalIQ tokens are a
            separate meter for platform usage — they are not the same thing.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-base font-semibold">How blocks work</h2>
          <ul className="list-disc space-y-1 pl-6 text-muted-foreground">
            <li>You choose one or two blocks at signup.</li>
            <li>
              You can add or remove blocks later in Settings — still capped at
              two.
            </li>
            <li>
              Within a block, a pause toggle stops automatic generation. A
              paused block does not run and does not charge.
            </li>
            <li>Only enabled blocks appear in your dashboard and sidebar.</li>
          </ul>
        </section>

        <section>
          <h2 className="mb-2 text-base font-semibold">
            What SignalIQ is not
          </h2>
          <ul className="list-disc space-y-1 pl-6 text-muted-foreground">
            <li>Not investment advice.</li>
            <li>Not betting advice.</li>
            <li>
              Statistical analysis, not predictions of certainty. Football
              model accuracy is probabilistic and publicly tracked.
            </li>
            <li>
              If you choose to bet, only stake what you can afford to lose.
            </li>
          </ul>
        </section>

        <p className="text-xs text-muted-foreground">
          By continuing you confirm that you have had the opportunity to read
          our{" "}
          <Link href="/legal/terms" className="underline">
            Terms of Service
          </Link>{" "}
          and{" "}
          <Link href="/legal/privacy" className="underline">
            Privacy Policy
          </Link>
          .
        </p>

        <label className="flex items-start gap-3 rounded-md border border-border p-3">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={acknowledged}
            onChange={(e) => setAcknowledged(e.target.checked)}
          />
          <span className="text-sm">
            I have read and understood the above.
          </span>
        </label>

        {error && <p className="text-sm text-destructive">{error}</p>}

        <Button
          type="button"
          className="w-full"
          disabled={!acknowledged || loading}
          onClick={onContinue}
        >
          {loading ? "Saving…" : "Continue"}
        </Button>
      </CardContent>
    </Card>
  );
}
