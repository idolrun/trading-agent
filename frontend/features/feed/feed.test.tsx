import { QueryClientProvider } from "@tanstack/react-query"
import { act, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { afterEach, describe, expect, it, vi } from "vitest"
import { FeedScreen } from "@/features/feed/feed-screen"
import type { FeedItem } from "@/features/feed/types"
import { applyEvent } from "@/features/live/event-map"
import { makeQueryClient } from "@/lib/query/get-query-client"
import { feedFixture, server } from "@/test/msw"
import { renderWithQuery } from "@/test/render"

const item = (over: Partial<FeedItem> = {}): FeedItem => ({ ...feedFixture, ...over })
const serve = (items: FeedItem[]) =>
  server.use(http.get("*/api/v1/feed", () => HttpResponse.json({ items, nextCursor: null })))

afterEach(() => {
  Object.defineProperty(window, "scrollY", { value: 0, configurable: true })
})

describe("feed item", () => {
  it("shows every label and status as text, plus type, author and time in local zone with ET title", async () => {
    const labels = ["held_until_open", "stale", "late", "backfilled", "muted", "channel_not_configured", "failed"]
    serve([
      item({ messageId: "1", labels, status: "failed" }),
      item({ messageId: "2", status: "received" }),
      item({ messageId: "3", status: "processing" }),
      item({ messageId: "4", status: "stale" }),
      item({ messageId: "5", status: null, labels: ["some_new_code"] }),
    ])
    renderWithQuery(<FeedScreen />)
    await screen.findAllByRole("article")
    for (const text of [
      "Held until open",
      "Late",
      "Backfilled",
      "Muted",
      "Channel not configured",
      "Received",
      "Processing",
      "Not queued",
      "Some new code",
    ]) {
      expect(screen.getAllByText(text).length).toBeGreaterThan(0)
    }
    const first = within(screen.getAllByRole("article")[0])
    expect(first.getAllByText("Failed")).toHaveLength(2) // label and status
    expect(first.getByText("Stale")).toBeInTheDocument()
    expect(within(screen.getAllByRole("article")[3]).getByText("Stale")).toBeInTheDocument() // status
    expect(screen.getAllByText("Type: Entry").length).toBeGreaterThan(0)
    expect(screen.getAllByText("Provider 111222333444555666").length).toBeGreaterThan(0)
    expect(screen.getAllByRole("list", { name: "Status and labels" })).toHaveLength(5)
    expect(document.querySelector("time")).toHaveAttribute("title", "Sep 29, 2026, 10:00:00 AM ET")
  })

  it("marks edits, deletes, missing text and replies; links each card", async () => {
    const a = "0a1b2c3d-0000-4000-8000-0000000000aa"
    const b = "0a1b2c3d-0000-4000-8000-0000000000bb"
    serve([
      item({ messageId: "1", eventType: "edit", eventSeq: 1, text: "edited text", replyToId: "77", cardIds: [a, b] }),
      item({ messageId: "2", eventType: "delete", eventSeq: 2, text: null, cardIds: [] }),
      item({ messageId: "3", text: null, cardIds: [] }),
    ])
    renderWithQuery(<FeedScreen />)
    await screen.findByText("edited text")
    expect(screen.getByText("Edited")).toBeInTheDocument()
    expect(screen.getByText("Deleted")).toBeInTheDocument()
    expect(screen.getByText("(deleted, no text)")).toBeInTheDocument()
    expect(screen.getByText("(no text)")).toBeInTheDocument()
    expect(screen.getByText("Reply to message 77")).toBeInTheDocument()
    const links = screen.getAllByRole("link", { name: /^Card / })
    expect(links.map((l) => l.getAttribute("href"))).toEqual([`/cards/${a}`, `/cards/${b}`])
  })
})

describe("feed screen", () => {
  it("shows the empty and error states with retry", async () => {
    serve([])
    const { unmount } = renderWithQuery(<FeedScreen />)
    expect(await screen.findByText("No messages")).toBeInTheDocument()
    unmount()

    let fail = true
    server.use(
      http.get("*/api/v1/feed", () =>
        fail ? HttpResponse.json({ code: "boom", message: "Feed down" }, { status: 400 }) : HttpResponse.json({ items: [item()], nextCursor: null }),
      ),
    )
    renderWithQuery(<FeedScreen />)
    expect(await screen.findByRole("alert")).toHaveTextContent("Feed down")
    fail = false
    await userEvent.click(screen.getByRole("button", { name: "Retry" }))
    expect(await screen.findByText("BTO SPY 550c 1.20")).toBeInTheDocument()
  })

  it("nothing is filtered by default; label and provider filters send their params", async () => {
    const seen: URLSearchParams[] = []
    server.use(
      http.get("*/api/v1/feed", ({ request }) => {
        seen.push(new URL(request.url).searchParams)
        return HttpResponse.json({ items: [item()], nextCursor: null })
      }),
    )
    renderWithQuery(<FeedScreen />)
    await screen.findByText("BTO SPY 550c 1.20")
    expect(seen[0].has("label")).toBe(false)
    expect(seen[0].has("providerId")).toBe(false)

    await userEvent.selectOptions(screen.getByLabelText("Label"), "muted")
    await waitFor(() => expect(seen.at(-1)?.get("label")).toBe("muted"))

    await userEvent.type(screen.getByLabelText("Provider ID"), "111222333444555666")
    await userEvent.click(screen.getByRole("button", { name: "Filter" }))
    await waitFor(() => expect(seen.at(-1)?.get("providerId")).toBe("111222333444555666"))
    expect(seen.at(-1)?.get("label")).toBe("muted")
  })

  it("rejects a non-numeric provider id without a request", async () => {
    renderWithQuery(<FeedScreen />)
    await screen.findByText("BTO SPY 550c 1.20")
    await userEvent.type(screen.getByLabelText("Provider ID"), "abc")
    expect(screen.getByRole("alert")).toHaveTextContent("Provider ID is a number.")
    expect(screen.getByRole("button", { name: "Filter" })).toBeDisabled()
  })

  it("Load more follows the cursor", async () => {
    const cursors: (string | null)[] = []
    server.use(
      http.get("*/api/v1/feed", ({ request }) => {
        const cursor = new URL(request.url).searchParams.get("cursor")
        cursors.push(cursor)
        return cursor
          ? HttpResponse.json({ items: [item({ messageId: "2", text: "older" })], nextCursor: null })
          : HttpResponse.json({ items: [item({ messageId: "1" })], nextCursor: "next-1" })
      }),
    )
    renderWithQuery(<FeedScreen />)
    await userEvent.click(await screen.findByRole("button", { name: "Load more" }))
    expect(await screen.findByText("older")).toBeInTheDocument()
    expect(cursors).toEqual([null, "next-1"])
    expect(screen.getAllByRole("article")).toHaveLength(2)
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument()
  })

  it("a new message at the top of the list appears at once; scrolled away it waits behind a pill", async () => {
    let items = [item({ messageId: "1", text: "first" })]
    serve(items)
    server.use(http.get("*/api/v1/feed", () => HttpResponse.json({ items, nextCursor: null })))
    const client = makeQueryClient()
    client.setDefaultOptions({ queries: { retry: false } })
    render(
      <QueryClientProvider client={client}>
        <FeedScreen />
      </QueryClientProvider>,
    )
    await screen.findByText("first")

    // at the top: no pill, the new row shows
    items = [item({ messageId: "2", text: "second" }), ...items]
    await act(async () => {
      await applyEvent(client, "feed.message", "2")
    })
    expect(await screen.findByText("second")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "New messages" })).not.toBeInTheDocument()

    // scrolled away: the row is held back
    Object.defineProperty(window, "scrollY", { value: 500, configurable: true })
    act(() => void window.dispatchEvent(new Event("scroll")))
    items = [item({ messageId: "3", text: "third" }), ...items]
    await act(async () => {
      await applyEvent(client, "feed.message", "3")
    })
    const pill = await screen.findByRole("button", { name: "New messages" })
    expect(screen.queryByText("third")).not.toBeInTheDocument()
    expect(within(screen.getByRole("main")).getAllByRole("article")).toHaveLength(2)

    const scrollTo = vi.fn()
    window.scrollTo = scrollTo as typeof window.scrollTo
    await userEvent.click(pill)
    expect(scrollTo).toHaveBeenCalledWith({ top: 0, behavior: "smooth" })
    expect(await screen.findByText("third")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "New messages" })).not.toBeInTheDocument()
  })
})
