import { describe, expect, it } from "vitest"
import { groupAlerts } from "@/features/alerts/group"
import { alertFixture, warningAlertFixture } from "@/test/msw"

const at = (n: number, extra = {}) => ({ ...alertFixture, id: `a${n}`, createdAt: `2026-09-29T14:0${n}:00Z`, ...extra })

describe("groupAlerts", () => {
  it("merges identical repeats and keeps every event, newest first", () => {
    const [group] = groupAlerts([at(1), at(3), at(2)])
    expect(group.events.map((e) => e.id)).toEqual(["a3", "a2", "a1"])
    expect(group.firstAt).toBe("2026-09-29T14:01:00Z")
    expect(group.lastAt).toBe("2026-09-29T14:03:00Z")
  })

  it("never merges a different message, card or severity", () => {
    const groups = groupAlerts([at(1), at(2, { message: "Feed silent for 600s" }), at(3, { cardId: "c1" }), at(4, { severity: "warning" })])
    expect(groups).toHaveLength(4)
  })

  it("orders open before acknowledged, then by severity", () => {
    const groups = groupAlerts([at(1, { acknowledged: true, message: "Old incident" }), warningAlertFixture, alertFixture])
    expect(groups.map((g) => g.severity)).toEqual(["urgent", "warning", "urgent"])
    expect(groups[2].open).toHaveLength(0)
  })
})
