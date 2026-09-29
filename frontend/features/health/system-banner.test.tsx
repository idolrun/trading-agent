import { screen } from "@testing-library/react"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import { FeedStatus } from "@/features/health/feed-status"
import { SystemBanner } from "@/features/health/system-banner"
import { healthFixture, server } from "@/test/msw"
import { renderWithQuery } from "@/test/render"

const withFlags = (flags: Record<string, boolean>) =>
  server.use(http.get("*/api/v1/health/details", () => HttpResponse.json({ ...healthFixture, flags })))

describe("SystemBanner", () => {
  it.each([
    ["feed_degraded", "alert", "Feed degraded"],
    ["entries_blocked", "alert", "New entries are blocked"],
    ["kill_switch", "alert", "Kill switch is on"],
    ["catch_up", "status", "Catching up"],
    ["parser_degraded", "status", "Parser degraded"],
  ])("%s renders a role=%s banner", async (flag, role, text) => {
    withFlags({ [flag]: true })
    renderWithQuery(<SystemBanner />)
    const banner = await screen.findByText(new RegExp(text))
    expect(banner).toHaveAttribute("data-flag", flag)
    expect(banner).toHaveAttribute("role", role)
  })

  it("ignores cleared and unknown flags and renders nothing", async () => {
    withFlags({ kill_switch: false, something_new: true })
    const { container } = renderWithQuery(
      <>
        <SystemBanner />
        <FeedStatus />
      </>,
    )
    await screen.findByText("Feed healthy")
    expect(container.querySelector("[data-flag]")).toBeNull()
  })
})
