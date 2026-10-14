"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api, getErrorMessage } from "@/lib/api";
import { useAuthStore } from "@/stores/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { COUNTRIES } from "@/lib/countries";

export default function RegisterPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [country, setCountry] = useState("");
  const [acceptedTerms, setAcceptedTerms] = useState(false);
  const [acceptedAge, setAcceptedAge] = useState(false);
  const [acceptedRisk, setAcceptedRisk] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const allAccepted = acceptedTerms && acceptedAge && acceptedRisk;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!allAccepted) {
      setError("You must accept all three acknowledgments to continue.");
      return;
    }
    if (username && (username.length < 3 || username.length > 64)) {
      setError("Username must be between 3 and 64 characters.");
      return;
    }
    setError(null);
    setLoading(true);
    try {
      const tokens = await api.register({
        email,
        username: username || undefined,
        password,
        full_name: fullName || undefined,
        country: country || undefined,
        accepted_terms: acceptedTerms,
        accepted_age: acceptedAge,
        accepted_risk: acceptedRisk,
      });
      useAuthStore.getState().setTokens(tokens.access_token, tokens.refresh_token);
      const me = await api.me();
      useAuthStore.getState().setMe(me);
      // S.7.5a — set middleware cookies, route based on onboarding state.
      const { setAuthedCookie, setOnboardedCookie } = await import("@/lib/cookies");
      setAuthedCookie(true);
      setOnboardedCookie(!me.onboarding_required);
      router.push(me.onboarding_required ? "/onboarding/guide" : "/dashboard");
    } catch (err) {
      setError(getErrorMessage(err, "Registration failed"));
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Create your account</CardTitle>
        <CardDescription>
          You get 1,000,000 free usage tokens on signup
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} className="space-y-4">
          <div>
            <label className="mb-1 block text-sm font-medium">Email</label>
            <Input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
            />
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium">
              Username <span className="text-muted-foreground">(used to sign in)</span>
            </label>
            <Input
              required
              value={username}
              onChange={(e) =>
                setUsername(e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, ""))
              }
              placeholder="e.g. johndoe"
              minLength={3}
              maxLength={64}
              pattern="^[a-z0-9_-]{3,64}$"
              autoComplete="username"
            />
            <p className="mt-1 text-xs text-muted-foreground">
              3-64 characters, letters, numbers, dashes, or underscores
            </p>
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium">Password</label>
            <Input
              type="password"
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
            />
            <p className="mt-1 text-xs text-muted-foreground">
              At least 8 characters
            </p>
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium">
              Full name <span className="text-muted-foreground">(optional)</span>
            </label>
            <Input
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              autoComplete="name"
            />
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium">
              Country <span className="text-muted-foreground">(optional)</span>
            </label>
            <select
              value={country}
              onChange={(e) => setCountry(e.target.value)}
              className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            >
              <option value="">Select a country…</option>
              {COUNTRIES.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </div>

          {/* Required acknowledgments */}
          <div className="rounded-md border border-border bg-muted/30 p-3 space-y-3 text-sm">
            <p className="text-xs uppercase tracking-wider text-muted-foreground">
              Required acknowledgments
            </p>

            <label className="flex items-start gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={acceptedTerms}
                onChange={(e) => setAcceptedTerms(e.target.checked)}
                className="mt-0.5 h-4 w-4 rounded border-border"
              />
              <span className="text-xs leading-relaxed">
                I accept the{" "}
                <Link href="/terms" className="underline">
                  Terms of Service
                </Link>{" "}
                and{" "}
                <Link href="/privacy" className="underline">
                  Privacy Policy
                </Link>
                .
              </span>
            </label>

            <label className="flex items-start gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={acceptedAge}
                onChange={(e) => setAcceptedAge(e.target.checked)}
                className="mt-0.5 h-4 w-4 rounded border-border"
              />
              <span className="text-xs leading-relaxed">
                I confirm I am <strong>18 years or older</strong> (or the age
                of majority in my jurisdiction).
              </span>
            </label>

            <label className="flex items-start gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={acceptedRisk}
                onChange={(e) => setAcceptedRisk(e.target.checked)}
                className="mt-0.5 h-4 w-4 rounded border-border"
              />
              <span className="text-xs leading-relaxed">
                I understand that SignalIQ provides{" "}
                <strong>statistical analysis, not betting advice</strong>.
                Football matches are inherently unpredictable. I make my own
                decisions with my own money and accept full responsibility
                for any outcome.
              </span>
            </label>
          </div>

          {error && <p className="text-sm text-destructive">{error}</p>}

          <Button
            type="submit"
            className="w-full"
            disabled={loading || !allAccepted}
          >
            {loading ? "Creating..." : "Create account"}
          </Button>
          <p className="text-center text-sm text-muted-foreground">
            Already have an account?{" "}
            <Link href="/login" className="underline">
              Sign in
            </Link>
          </p>
        </form>
      </CardContent>
    </Card>
  );
}
