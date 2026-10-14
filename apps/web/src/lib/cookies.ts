/**
 * Auth state cookies for the middleware.
 *
 * Two flag cookies, separate from the zustand/localStorage auth store:
 *
 *   signaliq_authed=1     — a valid access token exists
 *   signaliq_onboarded=1  — the org completed onboarding
 *
 * Why separate from localStorage: middleware runs on the edge before
 * the client bundle, so it cannot read localStorage. It CAN read
 * cookies. The localStorage copy remains the client source of truth
 * for the access token; the cookies are only state flags.
 */
const MAX_AGE = 60 * 60 * 24 * 30; // 30 days

function setFlag(name: string, on: boolean) {
  if (typeof document === "undefined") return;
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  if (on) {
    document.cookie = `${name}=1; path=/; max-age=${MAX_AGE}; SameSite=Lax${secure}`;
  } else {
    document.cookie = `${name}=; path=/; max-age=0; SameSite=Lax${secure}`;
  }
}

export function setAuthedCookie(on: boolean) {
  setFlag("signaliq_authed", on);
}

export function setOnboardedCookie(on: boolean) {
  setFlag("signaliq_onboarded", on);
}

export function clearAuthCookies() {
  setAuthedCookie(false);
  setOnboardedCookie(false);
}
