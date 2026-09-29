import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import { AuditLog } from "@/features/users/audit-log"
import { UserManager } from "@/features/users/user-manager"
import { server } from "@/test/msw"
import { renderWithQuery } from "@/test/render"

const user = (over: object = {}) => ({
  userId: "u-1",
  email: "me@example.com",
  isActive: true,
  mustChangePassword: false,
  createdAt: "2026-09-01T00:00:00Z",
  updatedAt: "2026-09-01T00:00:00Z",
  lastLoginAt: null,
  ...over,
})

const session = { email: "me@example.com", expiresAt: "2026-09-30T00:00:00Z", mustChangePassword: false }

describe("user manager", () => {
  it("adds a user and shows the temporary password once", async () => {
    let users = [user()]
    let body: unknown
    server.use(
      http.get("*/api/v1/auth/session", () => HttpResponse.json(session)),
      http.get("*/api/v1/users", () => HttpResponse.json(users)),
      http.post("*/api/v1/users", async ({ request }) => {
        body = await request.json()
        const created = user({ userId: "u-2", email: "new@example.com", mustChangePassword: true })
        users = [...users, created]
        return HttpResponse.json({ user: created, temporaryPassword: "Tmp-secret-123" }, { status: 201 })
      }),
    )
    renderWithQuery(<UserManager />)
    expect(await screen.findByText("me@example.com")).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText("Add a user by email"), "new@example.com")
    await userEvent.click(screen.getByRole("button", { name: "Add user" }))

    const dialog = await screen.findByRole("dialog")
    expect(within(dialog).getByText("Tmp-secret-123")).toBeInTheDocument()
    expect(body).toEqual({ email: "new@example.com" })
    await userEvent.click(within(dialog).getByRole("button", { name: "Done" }))
    await waitFor(() => expect(screen.queryByText("Tmp-secret-123")).not.toBeInTheDocument())
    expect(await screen.findByText("new@example.com")).toBeInTheDocument()
  })

  it("cannot deactivate yourself, can deactivate others and reset passwords", async () => {
    const calls: string[] = []
    server.use(
      http.get("*/api/v1/auth/session", () => HttpResponse.json(session)),
      http.get("*/api/v1/users", () => HttpResponse.json([user(), user({ userId: "u-2", email: "b@example.com" })])),
      http.post("*/api/v1/users/u-2/deactivate", () => {
        calls.push("deactivate")
        return HttpResponse.json(user({ userId: "u-2", email: "b@example.com", isActive: false }))
      }),
      http.post("*/api/v1/users/u-2/reset-password", () => {
        calls.push("reset")
        return HttpResponse.json({ user: user({ userId: "u-2" }), temporaryPassword: "Again-456" })
      }),
    )
    renderWithQuery(<UserManager />)
    const mine = (await screen.findByText("me@example.com")).closest("li")!
    expect(within(mine).getByRole("button", { name: "Deactivate" })).toBeDisabled()
    const other = screen.getByText("b@example.com").closest("li")!
    await userEvent.click(within(other).getByRole("button", { name: "Deactivate" }))
    await waitFor(() => expect(calls).toEqual(["deactivate"]))
    await userEvent.click(within(other).getByRole("button", { name: "Reset password" }))
    expect(await screen.findByText("Again-456")).toBeInTheDocument()
  })
})

describe("audit log", () => {
  it("lists entries and filters by action", async () => {
    const seen: (string | null)[] = []
    server.use(
      http.get("*/api/v1/audit", ({ request }) => {
        seen.push(new URL(request.url).searchParams.get("action"))
        return HttpResponse.json({
          items: [
            {
              id: 1,
              at: "2026-09-29T10:00:00Z",
              userId: "u-1",
              userEmail: "me@example.com",
              action: "trade_approved",
              resourceType: "card",
              resourceId: "c-1",
              ip: "127.0.0.1",
              requestId: "r-1",
              details: {},
            },
          ],
          nextCursor: null,
        })
      }),
    )
    renderWithQuery(<AuditLog />)
    expect(await screen.findByText("trade approved")).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByLabelText("Action"), "logout")
    await waitFor(() => expect(seen).toContain("logout"))
  })
})
