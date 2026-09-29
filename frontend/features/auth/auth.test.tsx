import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { EnrollScreen } from "@/features/auth/enroll-screen"
import { LoginScreen } from "@/features/auth/login-screen"
import { RequireSession } from "@/features/auth/require-session"
import { safeNext } from "@/features/auth/use-session"
import { onUnauthorized } from "@/lib/api/client"
import { server } from "@/test/msw"
import { renderWithQuery } from "@/test/render"

const nav = vi.hoisted(() => ({ replace: vi.fn(), query: "" }))
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: nav.replace }),
  useSearchParams: () => new URLSearchParams(nav.query),
}))

const webauthn = vi.hoisted(() => ({ startAuthentication: vi.fn(), startRegistration: vi.fn() }))
vi.mock("@simplewebauthn/browser", () => webauthn)

const notAllowed = () => Object.assign(new Error("denied"), { name: "NotAllowedError" })
const apiError = (status: number, code: string, message: string) =>
  HttpResponse.json({ code, message }, { status })

const session = { expiresAt: "2026-09-30T00:00:00Z", stepUpUntil: null }

beforeEach(() => {
  nav.replace.mockReset()
  nav.query = ""
  webauthn.startAuthentication.mockReset()
  webauthn.startRegistration.mockReset()
  onUnauthorized(undefined)
  vi.stubGlobal("PublicKeyCredential", class {})
  server.use(
    http.post("*/api/v1/auth/login/options", () => HttpResponse.json({ challenge: "c" })),
    http.post("*/api/v1/auth/register/options", () => HttpResponse.json({ challenge: "c" })),
  )
})

describe("login", () => {
  it("signs in and redirects to a safe next path", async () => {
    nav.query = "next=%2Fcards%2F1"
    webauthn.startAuthentication.mockResolvedValue({ id: "cred" })
    let body: unknown
    server.use(
      http.post("*/api/v1/auth/login/verify", async ({ request }) => {
        body = await request.json()
        return HttpResponse.json(session)
      }),
    )
    renderWithQuery(<LoginScreen />)
    await userEvent.click(screen.getByRole("button", { name: "Sign in with passkey" }))
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/cards/1"))
    expect(webauthn.startAuthentication).toHaveBeenCalledWith({ optionsJSON: { challenge: "c" } })
    expect(body).toEqual({ credential: { id: "cred" } })
  })

  it("ignores an off-site next", () => {
    expect(safeNext("//evil.example")).toBe("/")
    expect(safeNext("https://evil.example")).toBe("/")
    expect(safeNext("/\\evil.example")).toBe("/")
    expect(safeNext("/login")).toBe("/")
    expect(safeNext("/cards/1?x=1")).toBe("/cards/1?x=1")
  })

  it("shows a neutral message when the prompt is cancelled", async () => {
    webauthn.startAuthentication.mockRejectedValue(notAllowed())
    renderWithQuery(<LoginScreen />)
    await userEvent.click(screen.getByRole("button", { name: "Sign in with passkey" }))
    const status = await screen.findByRole("status")
    expect(status).toHaveTextContent("Passkey prompt closed")
    expect(status).toHaveFocus()
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled()
    expect(nav.replace).not.toHaveBeenCalled()
  })

  it("shows the API error message and stays on the page", async () => {
    webauthn.startAuthentication.mockResolvedValue({ id: "cred" })
    server.use(
      http.post("*/api/v1/auth/login/verify", () => apiError(401, "unauthorized", "passkey verification failed")),
    )
    renderWithQuery(<LoginScreen />)
    await userEvent.click(screen.getByRole("button", { name: "Sign in with passkey" }))
    const alert = await screen.findByRole("alert")
    expect(alert).toHaveTextContent("passkey verification failed")
    expect(alert).toHaveFocus()
    expect(nav.replace).not.toHaveBeenCalled() // a rejected passkey is not "session expired"
  })

  it("explains when the browser has no passkey support", () => {
    vi.unstubAllGlobals()
    renderWithQuery(<LoginScreen />)
    expect(screen.getByRole("alert")).toHaveTextContent("does not support passkeys")
    expect(screen.queryByRole("button")).not.toBeInTheDocument()
  })
})

describe("enroll", () => {
  it("reads the token, sends the label and goes to login", async () => {
    nav.query = "token=tok-1"
    webauthn.startRegistration.mockResolvedValue({ id: "new-cred" })
    const bodies: unknown[] = []
    server.use(
      http.post("*/api/v1/auth/register/options", async ({ request }) => {
        bodies.push(await request.json())
        return HttpResponse.json({ challenge: "c" })
      }),
      http.post("*/api/v1/auth/register/verify", async ({ request }) => {
        bodies.push(await request.json())
        return HttpResponse.json({ credentialId: "abc" })
      }),
    )
    renderWithQuery(<EnrollScreen />)
    await userEvent.type(screen.getByLabelText("Device name"), "My phone")
    await userEvent.click(screen.getByRole("button", { name: "Create passkey" }))
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/login"))
    expect(bodies).toEqual([
      { enrollmentToken: "tok-1", label: "My phone" },
      { enrollmentToken: "tok-1", credential: { id: "new-cred" } },
    ])
  })

  it("explains a missing token", () => {
    renderWithQuery(<EnrollScreen />)
    expect(screen.getByRole("alert")).toHaveTextContent("no token")
  })

  it("surfaces an invalid token error", async () => {
    nav.query = "token=bad"
    server.use(
      http.post("*/api/v1/auth/register/options", () =>
        apiError(401, "unauthorized", "enrolment token is invalid, used or expired"),
      ),
    )
    renderWithQuery(<EnrollScreen />)
    await userEvent.type(screen.getByLabelText("Device name"), "x")
    await userEvent.click(screen.getByRole("button", { name: "Create passkey" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("invalid, used or expired")
  })
})

describe("session gate", () => {
  it("redirects to /login on 401", async () => {
    server.use(http.get("*/api/v1/auth/session", () => apiError(401, "unauthorized", "no session")))
    renderWithQuery(
      <RequireSession>
        <p>secret</p>
      </RequireSession>,
    )
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/login"))
    expect(screen.queryByText("secret")).not.toBeInTheDocument()
  })

  it("renders children and a sign-out button with a session, and logs out", async () => {
    server.use(
      http.get("*/api/v1/auth/session", () => HttpResponse.json(session)),
      http.post("*/api/v1/auth/logout", () => new HttpResponse(null, { status: 204 })),
    )
    renderWithQuery(
      <RequireSession>
        <p>secret</p>
      </RequireSession>,
    )
    expect(await screen.findByText("secret")).toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }))
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/login"))
  })
})
