import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { ChangePasswordScreen } from "@/features/auth/change-password-screen"
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

const apiError = (status: number, code: string, message: string) =>
  HttpResponse.json({ code, message }, { status })

const session = { email: "me@example.com", expiresAt: "2026-09-30T00:00:00Z", mustChangePassword: false }

beforeEach(() => {
  nav.replace.mockReset()
  nav.query = ""
  onUnauthorized(undefined)
})

async function signIn(email = "me@example.com", password = "correct horse battery") {
  await userEvent.type(screen.getByLabelText("Email"), email)
  await userEvent.type(screen.getByLabelText("Password"), password)
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }))
}

describe("login", () => {
  it("signs in with email and password and redirects to a safe next path", async () => {
    nav.query = "next=%2Fcards%2F1"
    let body: unknown
    server.use(
      http.post("*/api/v1/auth/login", async ({ request }) => {
        body = await request.json()
        return HttpResponse.json(session)
      }),
    )
    renderWithQuery(<LoginScreen />)
    await signIn(" me@example.com ")
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/cards/1"))
    expect(body).toEqual({ email: "me@example.com", password: "correct horse battery" })
  })

  it("sends a temporary password to the change-password page", async () => {
    server.use(http.post("*/api/v1/auth/login", () => HttpResponse.json({ ...session, mustChangePassword: true })))
    renderWithQuery(<LoginScreen />)
    await signIn()
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/change-password"))
  })

  it("ignores an off-site next", () => {
    expect(safeNext("//evil.example")).toBe("/")
    expect(safeNext("https://evil.example")).toBe("/")
    expect(safeNext("/\\evil.example")).toBe("/")
    expect(safeNext("/login")).toBe("/")
    expect(safeNext("/cards/1?x=1")).toBe("/cards/1?x=1")
  })

  it("shows and hides the password", async () => {
    renderWithQuery(<LoginScreen />)
    const password = screen.getByLabelText("Password")
    expect(password).toHaveAttribute("type", "password")
    await userEvent.click(screen.getByRole("button", { name: "Show password" }))
    expect(password).toHaveAttribute("type", "text")
    await userEvent.click(screen.getByRole("button", { name: "Hide password" }))
    expect(password).toHaveAttribute("type", "password")
  })

  it("shows the API error, focuses it and stays on the page", async () => {
    server.use(http.post("*/api/v1/auth/login", () => apiError(401, "unauthorized", "wrong email or password")))
    renderWithQuery(<LoginScreen />)
    await signIn()
    const alert = await screen.findByRole("alert")
    expect(alert).toHaveTextContent("wrong email or password")
    expect(alert).toHaveFocus()
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled()
    expect(nav.replace).not.toHaveBeenCalled() // a wrong password is not "session expired"
  })
})

describe("change password", () => {
  beforeEach(() => {
    server.use(http.get("*/api/v1/auth/session", () => HttpResponse.json({ ...session, mustChangePassword: true })))
  })

  it("checks the repeat, sends both passwords and goes to the desk", async () => {
    let body: unknown
    server.use(
      http.post("*/api/v1/auth/change-password", async ({ request }) => {
        body = await request.json()
        return new HttpResponse(null, { status: 204 })
      }),
    )
    renderWithQuery(<ChangePasswordScreen />)
    await userEvent.type(screen.getByLabelText("Current password"), "temp-pass")
    await userEvent.type(screen.getByLabelText(/^New password/), "a much longer password")
    await userEvent.type(screen.getByLabelText("Repeat new password"), "a much longer passwor")
    expect(screen.getByRole("alert")).toHaveTextContent("do not match")
    expect(screen.getByRole("button", { name: "Change password" })).toBeDisabled()
    await userEvent.type(screen.getByLabelText("Repeat new password"), "d")
    await userEvent.click(screen.getByRole("button", { name: "Change password" }))
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/"))
    expect(body).toEqual({ currentPassword: "temp-pass", newPassword: "a much longer password" })
  })

  it("shows a server rejection", async () => {
    server.use(
      http.post("*/api/v1/auth/change-password", () => apiError(401, "unauthorized", "current password is wrong")),
    )
    renderWithQuery(<ChangePasswordScreen />)
    await userEvent.type(screen.getByLabelText("Current password"), "x")
    await userEvent.type(screen.getByLabelText(/^New password/), "a much longer password")
    await userEvent.type(screen.getByLabelText("Repeat new password"), "a much longer password")
    await userEvent.click(screen.getByRole("button", { name: "Change password" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("current password is wrong")
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

  it("holds the desk back until a temporary password is replaced", async () => {
    server.use(http.get("*/api/v1/auth/session", () => HttpResponse.json({ ...session, mustChangePassword: true })))
    renderWithQuery(
      <RequireSession>
        <p>secret</p>
      </RequireSession>,
    )
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/change-password"))
    expect(screen.queryByText("secret")).not.toBeInTheDocument()
  })

  it("renders children, and the account menu holds the email and sign-out, and logs out", async () => {
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
    await userEvent.click(screen.getByRole("button", { name: "Account menu" }))
    expect(await screen.findByText("me@example.com")).toHaveAttribute("title", "me@example.com")
    await userEvent.click(screen.getByRole("menuitem", { name: "Sign out" }))
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/login"))
  })
})
