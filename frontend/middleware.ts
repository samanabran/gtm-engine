import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const PUBLIC_PATHS = ["/login", "/register"];

function hasRefreshCookie(request: NextRequest) {
  return Boolean(
    request.cookies.get("gtm_refresh_token") ||
      request.cookies.get("refresh_token") ||
      request.cookies.get("__Host-gtm_refresh_token") ||
      request.cookies.get("__Host-refresh_token")
  );
}

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (pathname === "/") {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  const isPublicPath = PUBLIC_PATHS.some((path) => pathname.startsWith(path));
  if (
    !isPublicPath &&
    !hasRefreshCookie(request) &&
    process.env.NEXT_PUBLIC_DEMO_MODE !== "true"
  ) {
    return NextResponse.redirect(new URL("/login", request.url));
  }
}

export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico).*)"],
};
