/**
 * Auth state: tokens + current user.
 * Persisted to localStorage so refresh survives page reloads.
 */
import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

export type AuthUser = {
  id: string;
  email: string;
  full_name: string | null;
};

type AuthState = {
  accessToken: string | null;
  refreshToken: string | null;
  user: AuthUser | null;
  orgId: string | null;
  orgSlug: string | null;
  role: string | null;
  isHydrated: boolean;

  setTokens: (access: string, refresh: string) => void;
  setMe: (me: { user: AuthUser; org_id: string | null; org_slug: string | null; role: string | null }) => void;
  clear: () => void;
  tryRefresh: () => Promise<boolean>;
  setHydrated: () => void;
};

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      accessToken: null,
      refreshToken: null,
      user: null,
      orgId: null,
      orgSlug: null,
      role: null,
      isHydrated: false,

      setTokens: (access, refresh) => set({ accessToken: access, refreshToken: refresh }),
      setMe: (me) =>
        set({
          user: me.user,
          orgId: me.org_id,
          orgSlug: me.org_slug,
          role: me.role,
        }),
      clear: () =>
        set({
          accessToken: null,
          refreshToken: null,
          user: null,
          orgId: null,
          orgSlug: null,
          role: null,
        }),
      setHydrated: () => set({ isHydrated: true }),

      tryRefresh: async () => {
        const refresh = get().refreshToken;
        if (!refresh) return false;
        try {
          const res = await fetch("/api/auth/refresh", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ refresh_token: refresh }),
          });
          if (!res.ok) {
            get().clear();
            return false;
          }
          const data = await res.json();
          set({ accessToken: data.access_token, refreshToken: data.refresh_token });
          return true;
        } catch {
          get().clear();
          return false;
        }
      },
    }),
    {
      name: "signaliq-auth",
      storage: createJSONStorage(() => localStorage),
      partialize: (s) => ({
        accessToken: s.accessToken,
        refreshToken: s.refreshToken,
        user: s.user,
        orgId: s.orgId,
        orgSlug: s.orgSlug,
        role: s.role,
      }),
      onRehydrateStorage: () => (state) => {
        state?.setHydrated();
      },
    },
  ),
);
