import { NextRequest, NextResponse } from "next/server";

/**
 * Routing state machine for onboarding.
 *
 * Two cookies drive this:
 *   signaliq_authed=1     — user has a valid session
 *   signaliq_onboarded=1  — user has completed the guide + picker
 *
 * No backend calls. Every decision is a read of the two cookies.
 * Login sets `authed`; the picker sets `onboarded`; logout clears both.
 *
 * The matcher excludes /api, /_next, and static files. Intercepting
 * /api would break the very calls that tell us whether we're authed.
 */
export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  const authed = req.cookies.get("signaliq_authed")?.value === "1";
  const onboarded = req.cookies.get("signaliq_onboarded")?.value === "1";

  const isAuthPage = pathname === "/login" || pathname === "/register";
  const isOnboardingPage = pathname.startsWith("/onboarding");
  const isLegalPage = pathname.startsWith("/legal");
  const isRoot = pathname === "/";
  const isPublic = isAuthPage || isLegalPage || isRoot;

  // Case 1 — authed users should not see /login, /register, or /.
  if (authed && (isAuthPage || isRoot)) {
    const url = req.nextUrl.clone();
    url.pathname = onboarded ? "/dashboard" : "/onboarding/guide";
    return NextResponse.redirect(url);
  }

  // Case 2 — authed, not onboarded, hitting a protected route → guide.
  if (authed && !onboarded && !isOnboardingPage && !isPublic) {
    const url = req.nextUrl.clone();
    url.pathname = "/onboarding/guide";
    return NextResponse.redirect(url);
  }

  // Case 3 — onboarded users must not sit on /onboarding/*.
  if (authed && onboarded && isOnboardingPage) {
    const url = req.nextUrl.clone();
    url.pathname = "/dashboard";
    return NextResponse.redirect(url);
  }

  // Case 4 — unauthed on a protected route → /login.
  if (!authed && !isPublic && !isOnboardingPage) {
    const url = req.nextUrl.clone();
    url.pathname = "/login";
    return NextResponse.redirect(url);
  }

  // Case 5 — unauthed on /onboarding/* → /login. Can't be mid-flow
  // without a session.
  if (!authed && isOnboardingPage) {
    const url = req.nextUrl.clone();
    url.pathname = "/login";
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  // Match every page route, skip /api, /_next, and static assets.
  // /account/performance is a static HTML page under public/ —
  // skip middleware so it serves without an auth redirect.
  matcher: [
    "/((?!api|_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)",
  ],
};
