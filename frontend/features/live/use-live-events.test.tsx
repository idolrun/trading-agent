import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, renderHook } from "@testing-library/react"
import type { ReactNode } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { backoffMs, useLiveEvents } from "@/features/live/use-live-events"

class FakeEventSource {
  static instances: FakeEventSource[] = []
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  closed = false
  listeners = new Map<string, (event: Event) => void>()
  constructor(
    readonly url: string,
    readonly init?: EventSourceInit,
  ) {
    FakeEventSource.instances.push(this)
  }
  addEventListener(type: string, listener: (event: Event) => void) {
    this.listeners.set(type, listener)
  }
  close() {
    this.closed = true
  }
  emit(type: string, data: unknown) {
    this.listeners.get(type)?.(new MessageEvent(type, { data: JSON.stringify(data) }))
  }
}

function setup() {
  const client = new QueryClient()
  const spy = vi.spyOn(client, "invalidateQueries")
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  return { spy, ...renderHook(() => useLiveEvents(), { wrapper }) }
}

const latest = () => FakeEventSource.instances[FakeEventSource.instances.length - 1]

beforeEach(() => {
  FakeEventSource.instances = []
  vi.stubGlobal("EventSource", FakeEventSource)
  vi.useFakeTimers()
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe("useLiveEvents", () => {
  it("connects with credentials and invalidates on events", () => {
    const { result, spy } = setup()
    expect(latest().url).toBe("/api/v1/events")
    expect(latest().init).toEqual({ withCredentials: true })
    expect(result.current).toBe("connecting")

    act(() => latest().onopen?.())
    expect(result.current).toBe("live")
    expect(spy).not.toHaveBeenCalled() // first connect: nothing was missed

    act(() => latest().emit("card.updated", { id: "c-1" }))
    expect(spy.mock.calls).toEqual([[{ queryKey: ["cards", "list"] }], [{ queryKey: ["cards", "detail", "c-1"] }]])
  })

  it("reconnects with backoff and invalidates everything once it is back", () => {
    const { result, spy } = setup()
    act(() => latest().onopen?.())
    const first = latest()

    act(() => first.onerror?.())
    expect(first.closed).toBe(true)
    expect(result.current).toBe("reconnecting")
    expect(FakeEventSource.instances).toHaveLength(1)

    act(() => void vi.advanceTimersByTime(999))
    expect(FakeEventSource.instances).toHaveLength(1)
    act(() => void vi.advanceTimersByTime(1))
    expect(FakeEventSource.instances).toHaveLength(2)

    act(() => latest().onerror?.()) // second failure waits twice as long
    act(() => void vi.advanceTimersByTime(1_999))
    expect(FakeEventSource.instances).toHaveLength(2)
    act(() => void vi.advanceTimersByTime(1))
    expect(FakeEventSource.instances).toHaveLength(3)

    act(() => latest().onopen?.())
    expect(result.current).toBe("live")
    expect(spy.mock.calls).toEqual([[]]) // one unfiltered invalidation

    act(() => latest().onerror?.()) // backoff restarted at 1 s after a success
    act(() => void vi.advanceTimersByTime(1_000))
    expect(FakeEventSource.instances).toHaveLength(4)
  })

  it("resync invalidates everything and unmount closes the stream", () => {
    const { spy, unmount } = setup()
    act(() => latest().onopen?.())
    act(() => latest().emit("resync", { id: "" }))
    expect(spy.mock.calls).toEqual([[]])
    const source = latest()
    unmount()
    expect(source.closed).toBe(true)
  })

  it("caps the backoff at 30 s", () => {
    expect([0, 1, 2, 4].map(backoffMs)).toEqual([1_000, 2_000, 4_000, 16_000])
    expect(backoffMs(5)).toBe(30_000)
    expect(backoffMs(50)).toBe(30_000)
  })
})
