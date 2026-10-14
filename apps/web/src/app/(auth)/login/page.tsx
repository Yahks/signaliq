"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api, getErrorMessage } from "@/lib/api";
import { useAuthStore } from "@/stores/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export default function LoginPage() {
  const router = useRouter();
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const tokens = await api.login({ identifier, password });
      useAuthStore.getState().setTokens(tokens.access_token, tokens.refresh_token);
      const me = await api.me();
      useAuthStore.getState().setMe(me);
      // S.7.5a — set middleware cookies, route based on onboarding state.
      const { setAuthedCookie, setOnboardedCookie } = await import("@/lib/cookies");
      setAuthedCookie(true);
      setOnboardedCookie(!me.onboarding_required);
      router.push(me.onboarding_required ? "/onboarding/guide" : "/dashboard");
    } catch (err) {
      setError(getErrorMessage(err, "Login failed"));
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Sign in to SignalIQ</CardTitle>
        <CardDescription>Enter your identifier and password</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} className="space-y-4">
          <div>
            <label className="mb-1 block text-sm font-medium">Email or username</label>
            <Input type="identifier" required value={identifier} onChange={(e) => setIdentifier(e.target.value)} autoComplete="identifier" />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium">Password</label>
            <Input type="password" required value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
          <Button type="submit" className="w-full" disabled={loading}>
            {loading ? "Signing in..." : "Sign in"}
          </Button>
          <p className="text-center text-sm text-muted-foreground">
            No account? <Link href="/register" className="underline">Register</Link>
          </p>
        </form>
      </CardContent>
    </Card>
  );
}
