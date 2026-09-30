import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import { DiscordScreen } from "@/features/discord/discord-screen"
import { missingKeys } from "@/features/config/types"
import { configFixture, discordStatusFixture, server } from "@/test/msw"
import { renderWithQuery } from "@/test/render"

type PutBody = { expectedVersion: number; document: { channels: string[] }; reason: string }

function capturePuts(respond?: () => Response) {
  const bodies: PutBody[] = []
  server.use(
    http.put("*/api/v1/config", async ({ request }) => {
      const body = (await request.json()) as PutBody
      bodies.push(body)
      return respond?.() ?? HttpResponse.json({ version: body.expectedVersion + 1, document: body.document, missingForEntries: missingKeys(body.document) })
    }),
  )
  return bodies
}

const status = (over: Partial<typeof discordStatusFixture>) =>
  server.use(http.get("*/api/v1/discord/status", () => HttpResponse.json({ ...discordStatusFixture, ...over })))

describe("DiscordScreen", () => {
  it("shows the connection and the watched channel with its resolved name", async () => {
    renderWithQuery(<DiscordScreen />)
    expect(await screen.findByText("Connected")).toBeInTheDocument()
    expect(screen.getByText("signals#0001")).toBeInTheDocument()
    expect(screen.getByText("#alerts")).toBeInTheDocument()
    expect(screen.getByText(/Owls/)).toBeInTheDocument()
  })

  it("explains how to enable the listener when no token is set", async () => {
    status({ state: "not_configured", account: null, lastMessageAt: null, channels: [] })
    renderWithQuery(<DiscordScreen />)
    expect(await screen.findByText("Not configured")).toBeInTheDocument()
    expect(screen.getByText(/DISCORD_TOKEN/)).toBeInTheDocument()
    expect(screen.getByText("No messages received yet")).toBeInTheDocument()
  })

  it("flags a rejected token", async () => {
    status({ state: "login_failed" })
    renderWithQuery(<DiscordScreen />)
    expect(await screen.findByRole("alert")).toHaveTextContent("Discord rejected the token")
  })

  it("flags a channel the account cannot see", async () => {
    status({ channels: [{ id: "111222333444555666", name: null, guild: null, visible: false }] })
    renderWithQuery(<DiscordScreen />)
    expect(await screen.findByText(/not visible to this account/)).toBeInTheDocument()
  })

  it("adds a channel by saving the full config document against the current version", async () => {
    const bodies = capturePuts()
    renderWithQuery(<DiscordScreen />)
    await userEvent.type(await screen.findByLabelText("Channel ID"), " 123456789012345678 ")
    await userEvent.click(screen.getByRole("button", { name: "Add channel" }))
    await waitFor(() => expect(bodies).toHaveLength(1))
    expect(bodies[0].expectedVersion).toBe(configFixture.version)
    expect(bodies[0].document.channels).toEqual(["999888777666555444", "123456789012345678"])
    expect(bodies[0].document).toMatchObject({ feed_silence_s: 60 })
    expect(bodies[0].reason).toBe("Watch Discord channel 123456789012345678")
  })

  it("removes a channel", async () => {
    const bodies = capturePuts()
    renderWithQuery(<DiscordScreen />)
    await userEvent.click(await screen.findByRole("button", { name: "Remove 999888777666555444" }))
    await waitFor(() => expect(bodies).toHaveLength(1))
    expect(bodies[0].document.channels).toEqual([])
    expect(bodies[0].reason).toBe("Stop watching Discord channel 999888777666555444")
  })

  it("rejects an invalid id without calling the server", async () => {
    const bodies = capturePuts()
    renderWithQuery(<DiscordScreen />)
    await userEvent.type(await screen.findByLabelText("Channel ID"), "abc")
    expect(screen.getByRole("alert")).toHaveTextContent("10 to 32 digits")
    expect(screen.getByRole("button", { name: "Add channel" })).toBeDisabled()
    expect(bodies).toHaveLength(0)
  })

  it("shows the server error when the config changed underneath", async () => {
    capturePuts(() => HttpResponse.json({ code: "conflict", message: "config version is stale" }, { status: 409 }))
    renderWithQuery(<DiscordScreen />)
    await userEvent.type(await screen.findByLabelText("Channel ID"), "123456789012345678")
    await userEvent.click(screen.getByRole("button", { name: "Add channel" }))
    expect(await screen.findByText("config version is stale")).toBeInTheDocument()
  })
})
