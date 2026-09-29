import { http, HttpResponse } from "msw"
import { afterEach, describe, expect, it, vi } from "vitest"
import { apiGet, apiPost, onStepUpRequired, onUnauthorized } from "@/lib/api/client"
import { isApiError } from "@/lib/api/errors"
import { server } from "@/test/msw"

afterEach(() => {
  onUnauthorized(undefined)
  onStepUpRequired(undefined)
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
    await expect(apiGet("/api/v1/cards")).rejects.toMatchObject({ status: 0, code: "network" })
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

  it("calls the step-up hook only for 403 step_up_required", async () => {
    const stepUp = vi.fn()
    onStepUpRequired(stepUp)
    server.use(
      http.post("*/api/v1/flatten", () =>
        HttpResponse.json({ message: "Step up", code: "step_up_required" }, { status: 403 }),
      ),
      http.post("*/api/v1/forbidden", () =>
        HttpResponse.json({ message: "No", code: "forbidden" }, { status: 403 }),
      ),
    )
    await expect(apiPost("/api/v1/flatten")).rejects.toMatchObject({ code: "step_up_required" })
    await expect(apiPost("/api/v1/forbidden")).rejects.toMatchObject({ code: "forbidden" })
    expect(stepUp).toHaveBeenCalledOnce()
  })
})
