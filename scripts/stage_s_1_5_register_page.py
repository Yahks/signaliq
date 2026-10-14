"""
SignalIQ — Phase S.1.5: Register page — username + country, drop org.

Rewrites the register form to match the new backend contract:
  - email, username, password, full_name, country
  - no org_name / org_slug (auto-derived server-side)

Ships a countries.ts for the country dropdown.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "apps" / "web"
REGISTER_PAGE = WEB / "src" / "app" / "(auth)" / "register" / "page.tsx"
COUNTRIES = WEB / "src" / "lib" / "countries.ts"
API_TS = WEB / "src" / "lib" / "api.ts"


# ─────────────────────────────────────────────────────────────────────
# 1. countries.ts — a solid list of ISO country names
# ─────────────────────────────────────────────────────────────────────

COUNTRIES_SRC = '''// ISO 3166-1 country list — display names only, no codes.
// Used by the register form's country dropdown. Not exhaustive — the
// top ~120 by population + every UN member in one form or another.
// Extend freely; nothing depends on ordering or codes.

export const COUNTRIES: string[] = [
  "Afghanistan", "Albania", "Algeria", "Andorra", "Angola", "Argentina",
  "Armenia", "Australia", "Austria", "Azerbaijan", "Bahamas", "Bahrain",
  "Bangladesh", "Barbados", "Belarus", "Belgium", "Belize", "Benin",
  "Bhutan", "Bolivia", "Bosnia and Herzegovina", "Botswana", "Brazil",
  "Brunei", "Bulgaria", "Burkina Faso", "Burundi", "Cambodia", "Cameroon",
  "Canada", "Cape Verde", "Central African Republic", "Chad", "Chile",
  "China", "Colombia", "Comoros", "Congo (Brazzaville)",
  "Congo (Kinshasa)", "Costa Rica", "Côte d'Ivoire", "Croatia", "Cuba",
  "Cyprus", "Czechia", "Denmark", "Djibouti", "Dominica",
  "Dominican Republic", "Ecuador", "Egypt", "El Salvador",
  "Equatorial Guinea", "Eritrea", "Estonia", "Eswatini", "Ethiopia",
  "Fiji", "Finland", "France", "Gabon", "Gambia", "Georgia", "Germany",
  "Ghana", "Greece", "Grenada", "Guatemala", "Guinea", "Guinea-Bissau",
  "Guyana", "Haiti", "Honduras", "Hong Kong", "Hungary", "Iceland",
  "India", "Indonesia", "Iran", "Iraq", "Ireland", "Israel", "Italy",
  "Jamaica", "Japan", "Jordan", "Kazakhstan", "Kenya", "Kiribati",
  "Kosovo", "Kuwait", "Kyrgyzstan", "Laos", "Latvia", "Lebanon",
  "Lesotho", "Liberia", "Libya", "Liechtenstein", "Lithuania",
  "Luxembourg", "Madagascar", "Malawi", "Malaysia", "Maldives", "Mali",
  "Malta", "Marshall Islands", "Mauritania", "Mauritius", "Mexico",
  "Micronesia", "Moldova", "Monaco", "Mongolia", "Montenegro", "Morocco",
  "Mozambique", "Myanmar", "Namibia", "Nauru", "Nepal", "Netherlands",
  "New Zealand", "Nicaragua", "Niger", "Nigeria", "North Korea",
  "North Macedonia", "Norway", "Oman", "Pakistan", "Palau", "Palestine",
  "Panama", "Papua New Guinea", "Paraguay", "Peru", "Philippines",
  "Poland", "Portugal", "Qatar", "Romania", "Russia", "Rwanda",
  "Saint Kitts and Nevis", "Saint Lucia", "Saint Vincent and the Grenadines",
  "Samoa", "San Marino", "São Tomé and Príncipe", "Saudi Arabia",
  "Senegal", "Serbia", "Seychelles", "Sierra Leone", "Singapore",
  "Slovakia", "Slovenia", "Solomon Islands", "Somalia", "South Africa",
  "South Korea", "South Sudan", "Spain", "Sri Lanka", "Sudan", "Suriname",
  "Sweden", "Switzerland", "Syria", "Taiwan", "Tajikistan", "Tanzania",
  "Thailand", "Timor-Leste", "Togo", "Tonga", "Trinidad and Tobago",
  "Tunisia", "Turkey", "Turkmenistan", "Tuvalu", "Uganda", "Ukraine",
  "United Arab Emirates", "United Kingdom", "United States", "Uruguay",
  "Uzbekistan", "Vanuatu", "Vatican City", "Venezuela", "Vietnam",
  "Yemen", "Zambia", "Zimbabwe",
];
'''


# ─────────────────────────────────────────────────────────────────────
# 2. Full rewrite of register/page.tsx
# ─────────────────────────────────────────────────────────────────────

REGISTER_SRC = '''"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
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
      router.push("/dashboard");
    } catch (err) {
      if (err instanceof ApiError) setError(err.message);
      else setError("Something went wrong");
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
'''


# ─────────────────────────────────────────────────────────────────────
# 3. api.ts — update register signature
# ─────────────────────────────────────────────────────────────────────

def step1_write_countries() -> None:
    print("\n[1/4] Write countries.ts")
    COUNTRIES.parent.mkdir(parents=True, exist_ok=True)
    COUNTRIES.write_text(COUNTRIES_SRC)
    print(f"  ✓ wrote {COUNTRIES.relative_to(ROOT)}")


def step2_rewrite_register_page() -> None:
    print("\n[2/4] Rewrite register page")
    REGISTER_PAGE.write_text(REGISTER_SRC)
    print(f"  ✓ wrote {REGISTER_PAGE.relative_to(ROOT)}")


def step3_update_api_ts() -> None:
    print("\n[3/4] Update api.ts register signature")

    src = API_TS.read_text()

    # Find the register method's inline type signature and update it
    import re
    # Pattern: register: (body: { email: ...; org_name: ...; org_slug: ...}) =>
    # We need to find where register is defined.
    # Likely shape: register: (body: {...}) => request<TokenResponse>("/api/auth/register", ...)

    # Simple approach: replace org_name / org_slug fields with username / country
    if "org_name" in src:
        # Try to find and replace the register signature
        m = re.search(
            r'(register:\s*\(body:\s*\{[^}]+\})',
            src, re.DOTALL,
        )
        if m:
            old_sig = m.group(1)
            # Extract the fields, drop org_name/org_slug, add username/country
            # Build a clean new signature
            new_sig = """register: (body: {
    email: string;
    username?: string;
    password: string;
    full_name?: string;
    country?: string;
    accepted_terms: boolean;
    accepted_age: boolean;
    accepted_risk: boolean;
  }"""
            src = src.replace(old_sig, new_sig, 1)
            API_TS.write_text(src)
            print("  ✓ api.ts: register signature updated")
        else:
            print("  ⚠ register signature not found — will need manual patch")
            print("    search for 'org_name' in api.ts:")
            for i, line in enumerate(src.splitlines(), 1):
                if "org_name" in line or "org_slug" in line:
                    print(f"    {i}: {line.strip()}")
    else:
        print("  ↷ api.ts: no org_name references found (already clean)")


def step4_typecheck() -> bool:
    print("\n[4/4] TypeScript check")
    r = subprocess.run(
        ["npx", "tsc", "--noEmit"],
        cwd=WEB, capture_output=True, text=True, timeout=180,
    )
    if r.returncode != 0:
        print("  ❌ TypeScript errors:")
        for line in r.stdout.splitlines()[:20]:
            print(f"      {line}")
        return False
    print("  ✓ TypeScript clean")
    return True


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase S.1.5: Register page — username + country")
    print("=" * 70)

    step1_write_countries()
    step2_rewrite_register_page()
    step3_update_api_ts()
    ok = step4_typecheck()

    print("=" * 70)
    if ok:
        print("✅ S.1.5 complete — register page updated")
        print()
        print("Open http://localhost:3000/register to verify")
        sys.exit(0)
    print("❌ S.1.5 failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
