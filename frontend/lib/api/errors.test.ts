import { AxiosError, AxiosHeaders } from "axios"
import { describe, expect, it } from "vitest"
import { apiErrorFrom, userFacingMessage } from "@/lib/api/errors"

const UNKNOWN_ORDER = "We couldn't confirm the order status. Check your order history before trying again."

function failed(status: number, data: unknown, headers?: Record<string, string>) {
  return new AxiosError(
    `Request failed with status code ${status}`,
    "ERR_BAD_RESPONSE",
    { headers: new AxiosHeaders() },
    undefined,
    {
      data,
      status,
      statusText: "",
      headers: new AxiosHeaders(headers),
      config: { headers: new AxiosHeaders() },
    },
  )
}

describe("apiErrorFrom", () => {
  it("drops traceback and database text", () => {
    const traceback = apiErrorFrom(failed(500, { message: "Traceback (most recent call last): ValueError", code: "internal" }))
    const sql = apiErrorFrom(failed(500, { message: "psycopg.OperationalError: password authentication failed", code: "internal" }))
    expect(traceback.message).toBe("Something went wrong on our end. Please try again shortly.")
    expect(sql.message).toBe("Something went wrong on our end. Please try again shortly.")
    expect(traceback.message).not.toMatch(/Traceback|psycopg|Request failed/)
  })

  it("ignores a validation payload that is not a string", () => {
    const error = apiErrorFrom(failed(422, { detail: [{ loc: ["body", "qty"], msg: "field required", type: "missing" }] }))
    expect(error).toMatchObject({
      status: 422,
      ambiguous: false,
      message: "Some of the information you entered isn't valid. Check the fields and try again.",
    })
  })

  it("distinguishes a timeout from a dropped connection", () => {
    const timeout = apiErrorFrom(new AxiosError("timeout of 10000ms exceeded", "ECONNABORTED"))
    const offline = apiErrorFrom(new AxiosError("Network Error", "ERR_NETWORK"))
    expect(timeout).toMatchObject({
      status: 0,
      code: "timeout",
      ambiguous: true,
      message: "The request timed out. Please try again shortly.",
    })
    expect(offline).toMatchObject({ status: 0, code: "network", ambiguous: true })
    expect(offline.message).not.toBe("Network Error")
  })

  it("uses a generic fallback for an unexpected throw", () => {
    expect(apiErrorFrom(new Error("Request failed with status code 500"))).toMatchObject({
      status: 0,
      ambiguous: false,
      message: "Something went wrong. Please try again.",
    })
  })

  it("maps gateway failures without copying the axios sentence", () => {
    expect(apiErrorFrom(failed(502, "")).message).toBe("The service is temporarily unavailable. Please try again shortly.")
    expect(apiErrorFrom(failed(503, null)).message).toBe("The service is temporarily unavailable. Please try again shortly.")
    expect(apiErrorFrom(failed(504, "<html><body>504 Gateway Time-out</body></html>")).message).toBe(
      "The request timed out. Please try again shortly.",
    )
    expect(apiErrorFrom(failed(500, { message: "<html><body>Internal Server Error</body></html>" })).message).toBe(
      "Something went wrong on our end. Please try again shortly.",
    )
  })
})

describe("userFacingMessage", () => {
  it("does not claim an order failed when the outcome is ambiguous", () => {
    const server = apiErrorFrom(failed(500, { detail: "Internal Server Error" }))
    const timeout = apiErrorFrom(new AxiosError("timeout of 10000ms exceeded", "ETIMEDOUT"))
    const refused = apiErrorFrom(failed(422, { message: "entries are blocked", code: "invalid" }))

    expect(userFacingMessage(server, { order: true })).toBe(UNKNOWN_ORDER)
    expect(userFacingMessage(timeout, { order: true })).toBe(UNKNOWN_ORDER)
    expect(userFacingMessage(server)).toBe("Something went wrong on our end. Please try again shortly.")
    expect(userFacingMessage(refused, { order: true })).toBe("entries are blocked")
  })

  it("hides an unsafe message that did not come from the API client", () => {
    expect(userFacingMessage(new Error("Request failed with status code 500"))).toBe("Something went wrong. Please try again.")
    expect(userFacingMessage(new Error("wrong email or password"))).toBe("wrong email or password")
  })
})
