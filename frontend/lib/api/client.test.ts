import { http, HttpResponse } from "msw"
import { afterEach, describe, expect, it, vi } from "vitest"
import { apiGet, apiPost, onUnauthorized } from "@/lib/api/client"
import { isApiError } from "@/lib/api/errors"
import { server } from "@/test/msw"

afterEach(() => {
  onUnauthorized(undefined)
})

describe("api client errors", () => {
  it("maps {message, code} bodies to ApiError", async () => {
    server.use(
      http.get("*/api/v1/cards/x", () =>
        HttpResponse.json({ message: "Card not found", code: "not_found" }, { status: 404 }),
      ),
    )
    const error = await apiGet("/api/v1/cards/x").catch((e: unknown) => e)
    expect(isApiError(error)).toBe(true)
    expect(error).toMatchObject({ message: "Card not found", status: 404, code: "not_found" })
  })

  it("maps a dropped connection to a network error", async () => {
    server.use(http.get("*/api/v1/cards", () => HttpResponse.error()))
    await expect(apiGet("/api/v1/cards")).rejects.toMatchObject({
      status: 0,
      code: "network",
      ambiguous: true,
      message: "Unable to connect to the server. Check your internet connection and try again.",
    })
  })

  it("replaces a raw 500 when the body has no safe message", async () => {
    server.use(
      http.get("*/api/v1/cards/x", () => HttpResponse.json({ detail: "Internal Server Error" }, { status: 500 })),
      http.get("*/api/v1/cards/y", () => new HttpResponse(null, { status: 500 })),
    )
    await expect(apiGet("/api/v1/cards/x")).rejects.toMatchObject({
      status: 500,
      ambiguous: true,
      message: "Something went wrong on our end. Please try again shortly.",
    })
    await expect(apiGet("/api/v1/cards/y")).rejects.toMatchObject({
      status: 500,
      message: "Something went wrong on our end. Please try again shortly.",
    })
  })

  it("keeps a safe server message, including a 422 field list", async () => {
    server.use(
      http.get("*/api/v1/cards/x", () =>
        HttpResponse.json({ message: "Could not price the card", code: "internal" }, { status: 500 }),
      ),
      http.post("*/api/v1/config", () =>
        HttpResponse.json({ message: "body.document.risk: must be positive", code: "invalid" }, { status: 422 }),
      ),
    )
    await expect(apiGet("/api/v1/cards/x")).rejects.toMatchObject({ message: "Could not price the card", code: "internal" })
    await expect(apiPost("/api/v1/config")).rejects.toMatchObject({
      status: 422,
      ambiguous: false,
      message: "body.document.risk: must be positive",
    })
  })

  it("maps an empty 401 and 403 to session and permission copy", async () => {
    server.use(
      http.get("*/api/v1/session", () => new HttpResponse(null, { status: 401 })),
      http.get("*/api/v1/admin", () => new HttpResponse(null, { status: 403 })),
    )
    await expect(apiGet("/api/v1/session")).rejects.toMatchObject({
      status: 401,
      message: "Your session may have expired. Please sign in again.",
    })
    await expect(apiGet("/api/v1/admin")).rejects.toMatchObject({
      status: 403,
      message: "You don't have permission to perform this action.",
    })
  })

  it("mentions Retry-After when a 429 body has no message", async () => {
    server.use(
      http.get("*/api/v1/cards", () => new HttpResponse(null, { status: 429, headers: { "Retry-After": "2" } })),
    )
    await expect(apiGet("/api/v1/cards")).rejects.toMatchObject({
      status: 429,
      message: "You're making requests too quickly. Please wait 2 seconds and try again.",
    })
  })

  it("calls the 401 handler and still rejects", async () => {
    const handler = vi.fn()
    onUnauthorized(handler)
    server.use(
      http.get("*/api/v1/cards", () =>
        HttpResponse.json({ message: "Sign in", code: "unauthorized" }, { status: 401 }),
      ),
    )
    await expect(apiGet("/api/v1/cards")).rejects.toMatchObject({ status: 401, code: "unauthorized" })
    expect(handler).toHaveBeenCalledOnce()
  })
})
