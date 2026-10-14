"""S.6.4.1d — frontend: one row per key.

Part 4 of the credentials-per-key refactor. After this commit the
/settings/credentials page shows one row per key the user actually
added, not one row per (key, role) pair.

Two files:
  - apps/web/src/lib/api.ts — Credential.role → roles; input gains label
  - apps/web/src/app/(app)/settings/credentials/page.tsx — table columns,
    add form gains optional Label field, counter says 'keys' not
    'credentials'

Verified: tsc clean; the page renders 2 rows for the 2 existing keys.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API_TS = ROOT / "apps" / "web" / "src" / "lib" / "api.ts"
PAGE = ROOT / "apps" / "web" / "src" / "app" / "(app)" / "settings" / "credentials" / "page.tsx"
WEB = ROOT / "apps" / "web"


def fail(msg: str) -> None:
    raise SystemExit(f"\n❌ {msg}")


def patch(path: Path, old: str, new: str, label: str) -> None:
    src = path.read_text()
    if new.strip() and new.strip() in src and old not in src:
        print(f"  ↷ {path.name}: {label} already applied")
        return
    if old not in src:
        fail(f"{path.name}: pattern not found for '{label}'\n"
             f"--- expected ---\n{old[:500]}")
    path.write_text(src.replace(old, new, 1))
    if new not in path.read_text():
        fail(f"{path.name}: patch wrote but new not found")
    print(f"  ✓ {path.name}: {label}")


# ─────────────────────────────────────────────────────────────────────
# 1. api.ts — types
# ─────────────────────────────────────────────────────────────────────

def step1_api_ts() -> None:
    print("\n[1/3] api.ts — Credential type")

    patch(
        API_TS,
        '''export type Credential = {
  id: string;
  provider: string;
  model_name: string;
  role: string;
  label: string;
  key_preview: string;
  is_active: boolean;
  priority: number;
  exhausted_until: string | null;
  last_success_at: string | null;
  created_at: string;
};''',
        '''export type Credential = {
  id: string;
  provider: string;
  model_name: string;
  roles: string[];
  label: string;
  key_preview: string;
  is_active: boolean;
  priority: number;
  exhausted_until: string | null;
  last_success_at: string | null;
  created_at: string;
};''',
        "Credential: role → roles",
    )

    patch(
        API_TS,
        '''export type CreateCredentialInput = {
  provider: string;
  api_key: string;
};''',
        '''export type CreateCredentialInput = {
  provider: string;
  api_key: string;
  label?: string;
};''',
        "CreateCredentialInput: optional label",
    )


# ─────────────────────────────────────────────────────────────────────
# 2. page.tsx — state, mutation, form, table
# ─────────────────────────────────────────────────────────────────────

def step2_page_state() -> None:
    print("\n[2/3] page.tsx — state + mutation")

    # Add a `label` state next to apiKey
    patch(
        PAGE,
        '''  const [showForm, setShowForm] = useState(false);
  const [provider, setProvider] = useState("groq");
  const [apiKey, setApiKey] = useState("");''',
        '''  const [showForm, setShowForm] = useState(false);
  const [provider, setProvider] = useState("groq");
  const [apiKey, setApiKey] = useState("");
  // S.6.4.1d — optional user-supplied label. Empty string means
  // "let the backend derive {provider}-{key_hash4}".
  const [label, setLabel] = useState("");''',
        "add label state",
    )

    # Pass it to the mutation
    patch(
        PAGE,
        '''  const add = useMutation({
    mutationFn: () => api.addCredential({ provider, api_key: apiKey }),''',
        '''  const add = useMutation({
    mutationFn: () =>
      api.addCredential({
        provider,
        api_key: apiKey,
        label: label.trim() || undefined,
      }),''',
        "mutationFn passes label",
    )

    # Reset label on success
    patch(
        PAGE,
        '''      qc.invalidateQueries({ queryKey: ["credentials"] });
      setApiKey("");
      setShowForm(false);''',
        '''      qc.invalidateQueries({ queryKey: ["credentials"] });
      setApiKey("");
      setLabel("");
      setShowForm(false);''',
        "reset label on success",
    )


def step3_page_form() -> None:
    print("\n[3/3] page.tsx — form + table")

    # Add the Label field between Provider and API key
    patch(
        PAGE,
        '''            <div>
              <label className="mb-1 block text-sm font-medium">API key</label>
              <Input
                type="password"
                placeholder="Paste your API key here"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
              />''',
        '''            <div>
              <label className="mb-1 block text-sm font-medium">
                Label <span className="text-muted-foreground">(optional)</span>
              </label>
              <Input
                type="text"
                placeholder="My Groq key"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
              />
              <p className="mt-1 text-xs text-muted-foreground">
                Give this key a name if you&apos;ll have more than one from
                the same provider. Leave blank for an auto-generated name.
              </p>
            </div>

            <div>
              <label className="mb-1 block text-sm font-medium">API key</label>
              <Input
                type="password"
                placeholder="Paste your API key here"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
              />''',
        "add Label input",
    )

    # Table: drop Model + Role, add Roles served
    patch(
        PAGE,
        '''                      <td className="py-2 pr-3 font-medium">{c.label}</td>
                      <td className="py-2 pr-3 text-muted-foreground">{c.provider}</td>
                      <td className="py-2 pr-3 font-mono text-xs">{c.model_name}</td>
                      <td className="py-2 pr-3 text-muted-foreground">{c.role}</td>''',
        '''                      <td className="py-2 pr-3 font-medium">{c.label}</td>
                      <td className="py-2 pr-3 text-muted-foreground">{c.provider}</td>
                      <td className="py-2 pr-3 text-muted-foreground">
                        {(c.roles || []).join(", ") || "—"}
                      </td>''',
        "table row: Model + Role → Roles",
    )

    # Table header
    patch(
        PAGE,
        '''                    <th className="text-left py-2 pr-3 font-medium">Provider</th>
                    <th className="text-left py-2 pr-3 font-medium">Model</th>
                    <th className="text-left py-2 pr-3 font-medium">Role</th>''',
        '''                    <th className="text-left py-2 pr-3 font-medium">Provider</th>
                    <th className="text-left py-2 pr-3 font-medium">Roles served</th>''',
        "table header",
    )

    # Counter copy: credentials → keys
    patch(
        PAGE,
        '''            {items.length} credential{items.length === 1 ? "" : "s"}''',
        '''            {items.length} key{items.length === 1 ? "" : "s"}''',
        "counter copy",
    )

    # Empty state copy
    patch(
        PAGE,
        '''              No credentials yet. Add one above to enable generation.''',
        '''              No API keys yet. Add one above to enable generation.''',
        "empty state copy",
    )


# ─────────────────────────────────────────────────────────────────────
# Verify
# ─────────────────────────────────────────────────────────────────────

def verify() -> bool:
    print("\n" + "=" * 60)
    print("VERIFICATION")
    print("=" * 60)

    print("\n[A] tsc --noEmit")
    r = subprocess.run(
        ["./node_modules/.bin/tsc", "--noEmit"],
        cwd=WEB, capture_output=True, text=True, timeout=240,
    )
    if r.returncode != 0:
        print("  ❌ TS errors:")
        for line in (r.stdout or r.stderr).splitlines()[:20]:
            print(f"     {line}")
        return False
    print("  ✓ TypeScript clean")

    # Static check: no lingering `c.role` or `c.model_name`
    print("\n[B] no lingering singular-role references")
    src = PAGE.read_text()
    bad = []
    for needle in ("c.role ", "c.role}", "c.role,", "{c.role", ".model_name}"):
        if needle in src:
            bad.append(needle)
    if bad:
        print(f"  ❌ still present: {bad}")
        return False
    print("  ✓ page uses c.roles only")

    # The new label field is present
    if "setLabel" in src and "Label <span" in src:
        print("  ✓ Label field present in the form")
    else:
        print("  ⚠ Label field missing or renamed — check manually")

    return True


def main() -> None:
    print("=" * 70)
    print("S.6.4.1d — frontend: one row per key")
    print("=" * 70)

    step1_api_ts()
    step2_page_state()
    step3_page_form()
    ok = verify()

    print("=" * 70)
    if ok:
        print("✅ S.6.4.1d complete")
        print("   /settings/credentials now shows one row per key")
        print()
        print("   Browser check:")
        print("     • the page shows N rows where N = keys added")
        print("     • the add form has an optional Label field")
        print("     • the Roles column shows e.g. 'fast, reasoning'")
        sys.exit(0)
    print("❌ failed — see above")
    sys.exit(1)


if __name__ == "__main__":
    main()
