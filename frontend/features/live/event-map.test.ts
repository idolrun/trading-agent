import { QueryClient } from "@tanstack/react-query"
import { describe, expect, it, vi } from "vitest"
import { EVENT_TYPES, applyEvent, queryKeysFor } from "@/features/live/event-map"

const ID = "0a1b2c3d-0000-4000-8000-000000000001"

describe("event map", () => {
  it.each([
    ["card.created", [["cards", "list"]]],
    ["card.updated", [["cards", "list"], ["cards", "detail", ID]]],
    ["order.created", [["orders", "list"], ["orders", "detail", ID]]],
    ["order.updated", [["orders", "list"], ["orders", "detail", ID]]],
    ["fill.created", [["orders", "list"], ["lots", "list"], ["positions", "list"]]],
    ["lot.updated", [["lots", "list"], ["lots", "detail", ID], ["positions", "list"]]],
    ["position.updated", [["positions", "list"], ["positions", "detail", ID]]],
    ["alert.created", [["alerts", "list"]]],
    ["flag.changed", [["health"]]],
  ])("%s invalidates its domain keys", (type, keys) => {
    expect(queryKeysFor(type, ID)).toEqual(keys)
  })

  it("ignores unknown types", () => {
    expect(queryKeysFor("something.new", ID)).toEqual([])
  })

  it("subscribes to every mapped type and resync", () => {
    expect(EVENT_TYPES).toContain("resync")
    expect(EVENT_TYPES).toContain("card.updated")
  })

  it("applies each key as its own invalidation; resync invalidates everything", async () => {
    const client = new QueryClient()
    const spy = vi.spyOn(client, "invalidateQueries")
    await applyEvent(client, "card.updated", ID)
    expect(spy.mock.calls).toEqual([[{ queryKey: ["cards", "list"] }], [{ queryKey: ["cards", "detail", ID] }]])
    spy.mockClear()
    await applyEvent(client, "resync", "")
    expect(spy.mock.calls).toEqual([[]])
  })
})
