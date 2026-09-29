import { NextResponse, type NextRequest } from "next/server"

// Optimistic only: the cookie's presence is checked, its validity is the
// backend's call (the client gate handles a 401).
export function proxy(request: NextRequest) {
  if (request.cookies.has("session")) return NextResponse.next()
  const url = new URL("/login", request.url)
  const { pathname, search } = request.nextUrl
  if (pathname !== "/") url.searchParams.set("next", pathname + search)
  return NextResponse.redirect(url)
}

export const config = {
  // Skips /login, /api, /_next and any path with a dot (favicon.ico,
  // sw.js, manifest.webmanifest, images).
  matcher: ["/((?!(?:login|api|_next)(?:/|$)|.*\\..*).*)"],
}
