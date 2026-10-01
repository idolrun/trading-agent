import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import { ConfigScreen } from "@/features/config/config-screen"
import { defaultDocument, missingKeys } from "@/features/config/types"
import { configFixture, server } from "@/test/msw"
import { renderWithQuery } from "@/test/render"

type PutBody = { expectedVersion: number; document: Record<string, unknown>; reason: string }

/** Records PUT bodies; `respond` decides the reply (default: save as version + 1). */
function capturePuts(respond?: () => Response) {
  const bodies: PutBody[] = []
  server.use(
    http.put("*/api/v1/config", async ({ request }) => {
      const body = (await request.json()) as PutBody
      bodies.push(body)
      return (
        respond?.() ??
        HttpResponse.json({ version: body.expectedVersion + 1, document: body.document, missingForEntries: missingKeys(body.document) })
      )
    }),
  )
  return bodies
}

const saveButton = () => screen.getByRole("button", { name: "Save config" })
const errorBody = (code: string, message: string) => HttpResponse.json({ code, message }, { status: code === "conflict" ? 409 : 422 })

async function save(reason = "because") {
  await userEvent.type(screen.getByLabelText("Reason for this change"), reason)
  await userEvent.click(saveButton())
}

describe("ConfigScreen", () => {
  it("highlights unset fields and lists them in the summary", async () => {
    renderWithQuery(<ConfigScreen />)

    const summary = await screen.findByRole("list", { name: "Missing for entries" })
    for (const key of ["max_daily_loss_usd", "max_total_exposure_usd", "llm_monthly_budget_usd"]) {
      expect(within(summary).getByText(key)).toBeInTheDocument()
    }
    expect(within(summary).queryByText("feed_silence_s")).not.toBeInTheDocument()

    for (const label of ["Max daily loss (USD)", "Max total exposure (USD)", "LLM monthly budget (USD)"]) {
      const input = screen.getByLabelText(label)
      expect(input).toHaveAttribute("data-unset", "true")
      expect(input).toHaveAccessibleDescription("not set — entries disabled")
    }
    expect(screen.getByLabelText("Feed silence alert (s)")).not.toHaveAttribute("data-unset")

    await userEvent.type(screen.getByLabelText("Max daily loss (USD)"), "500")
    expect(screen.getByLabelText("Max daily loss (USD)")).not.toHaveAttribute("data-unset")
  })

  it("saves the Laya fallback settings and keeps its thresholds", async () => {
    const puts = capturePuts()
    renderWithQuery(<ConfigScreen />)
    await userEvent.click(await screen.findByLabelText("Laya enabled"))
    await userEvent.clear(screen.getByLabelText("Laya t_type (0 to 1)"))
    await userEvent.type(screen.getByLabelText("Laya t_type (0 to 1)"), "0.95")
    await save("turn on the local fallback")

    await screen.findByText("Version 4")
    expect(puts[0].document).toMatchObject({
      laya: { enabled: true, timeout_ms: 10000, t_chat: "0.95", t_field: "0.9", t_type: "0.95", t_link: "0.9" },
    })
  })

  it("saves the full document with expectedVersion and reason", async () => {
    const puts = capturePuts()
    renderWithQuery(<ConfigScreen />)
    await screen.findByLabelText("Max daily loss (USD)")

    expect(saveButton()).toBeDisabled()
    await userEvent.type(screen.getByLabelText("Max daily loss (USD)"), "500")
    await userEvent.clear(screen.getByLabelText("Option tiers USD 1"))
    await userEvent.type(screen.getByLabelText("Option tiers USD 1"), "600")
    expect(saveButton()).toBeDisabled() // no reason yet
    await save("set risk limits")

    await screen.findByText("Version 4")
    expect(puts).toHaveLength(1)
    expect(puts[0].expectedVersion).toBe(3)
    expect(puts[0].reason).toBe("set risk limits")
    expect(Object.keys(puts[0].document).sort()).toEqual(Object.keys(configFixture.document).sort())
    expect(puts[0].document).toMatchObject({
      max_daily_loss_usd: "500",
      option_tiers: { default: "600", small: "250", lotto: "100" },
      channels: ["999888777666555444"],
      stock_tiers: null,
      feed_silence_s: 60,
      jev: { model: null, timeout_ms: 300, enabled: false },
    })
    // dirty state reset: reason cleared, nothing to save
    expect(screen.getByLabelText("Reason for this change")).toHaveValue("")
    expect(saveButton()).toBeDisabled()
  })

  it("on 409 refetches and offers reload or reapply", async () => {
    const serverDocument = { ...configFixture.document, stale_entry_s: 45, t_chat: "0.5" }
    const puts = capturePuts(() => errorBody("conflict", "config changed since expectedVersion"))
    let served = false
    server.use(
      http.get("*/api/v1/config", () =>
        HttpResponse.json(served ? { version: 4, document: serverDocument, missingForEntries: missingKeys(serverDocument) } : configFixture),
      ),
    )
    renderWithQuery(<ConfigScreen />)
    await screen.findByLabelText("Stale entry (s)")
    await userEvent.clear(screen.getByLabelText("Stale entry (s)"))
    await userEvent.type(screen.getByLabelText("Stale entry (s)"), "20")
    await userEvent.type(screen.getByLabelText("Max daily loss (USD)"), "500")
    served = true
    await save()

    const panel = await screen.findByText(/now version 4/)
    expect(panel).toBeInTheDocument()
    const table = screen.getByRole("table", { name: "Conflicting fields" })
    expect(within(within(table).getByRole("row", { name: /stale_entry_s/ })).getAllByRole("cell").map((c) => c.textContent)).toEqual(["20", "45"])
    expect(within(table).getByRole("row", { name: /max_daily_loss_usd/ })).toBeInTheDocument()
    expect(within(table).queryByRole("row", { name: /t_chat/ })).not.toBeInTheDocument() // server-only change
    expect(saveButton()).toBeDisabled()

    await userEvent.click(screen.getByRole("button", { name: "Reapply my edits" }))
    expect(screen.queryByText(/now version 4/)).not.toBeInTheDocument()
    expect(screen.getByLabelText("Stale entry (s)")).toHaveValue(20)
    expect(screen.getByLabelText("Chat threshold")).toHaveValue(0.5) // server's change kept
    await screen.findByText("Version 4")

    await userEvent.type(screen.getByLabelText("Reason for this change"), "retry")
    await userEvent.click(saveButton())
    await waitFor(() => expect(puts).toHaveLength(2))
    expect(puts[1].expectedVersion).toBe(4)
    expect(puts[1].document).toMatchObject({ stale_entry_s: 20, t_chat: "0.5", max_daily_loss_usd: "500" })
  })

  it("reload discards local edits on 409", async () => {
    const serverDocument = { ...configFixture.document, stale_entry_s: 45 }
    capturePuts(() => errorBody("conflict", "config changed"))
    let served = false
    server.use(
      http.get("*/api/v1/config", () =>
        HttpResponse.json(served ? { version: 4, document: serverDocument, missingForEntries: [] } : configFixture),
      ),
    )
    renderWithQuery(<ConfigScreen />)
    await userEvent.clear(await screen.findByLabelText("Stale entry (s)"))
    await userEvent.type(screen.getByLabelText("Stale entry (s)"), "20")
    served = true
    await save()
    await userEvent.click(await screen.findByRole("button", { name: /Reload/ }))
    expect(screen.getByLabelText("Stale entry (s)")).toHaveValue(45)
    expect(screen.queryByRole("list", { name: "Missing for entries" })).not.toBeInTheDocument()
  })

  it("maps a flat 422 message onto fields and shows the rest as general errors", async () => {
    capturePuts(() =>
      errorBody(
        "invalid",
        "body.document.stale_entry_s: Input should be greater than 0; body.document.llm.roles.extract.chain.0.model: String should have at least 1 character; body.reason: Field required",
      ),
    )
    renderWithQuery(<ConfigScreen />)
    await screen.findByLabelText("Stale entry (s)")
    await userEvent.type(screen.getByLabelText("Max daily loss (USD)"), "1")
    await save()

    const stale = screen.getByLabelText("Stale entry (s)")
    expect(await within(stale.parentElement!).findByText("Input should be greater than 0")).toBeInTheDocument()
    expect(stale).toHaveAttribute("aria-invalid", "true")
    const llm = screen.getByLabelText("LLM registry (JSON)")
    expect(within(llm.parentElement!).getByText("roles.extract.chain.0.model: String should have at least 1 character")).toBeInTheDocument()
    expect(screen.getByText("body.reason: Field required")).toBeInTheDocument()
    expect(screen.getAllByText("Input should be greater than 0")).toHaveLength(1)
    // edits are kept
    expect(screen.getByLabelText("Max daily loss (USD)")).toHaveValue(1)
  })

  it("blocks saving when the LLM text is not JSON", async () => {
    const puts = capturePuts()
    renderWithQuery(<ConfigScreen />)
    const llm = await screen.findByLabelText("LLM registry (JSON)")
    await userEvent.type(llm, "{{")
    expect(await screen.findByText(/Not valid JSON/)).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText("Reason for this change"), "x")
    expect(saveButton()).toBeDisabled()
    expect(puts).toHaveLength(0)
  })

  it("renders the change history", async () => {
    renderWithQuery(<ConfigScreen />)
    const history = await screen.findByRole("heading", { name: "History" })
    const items = await within(history.parentElement!).findAllByRole("listitem")
    expect(items).toHaveLength(2)
    expect(within(items[0]).getByText(/Version 3 · alice/)).toBeInTheDocument()
    expect(within(items[0]).getByText("tighten chase")).toBeInTheDocument()
    expect(within(items[0]).getByText("entry_chase_pct")).toBeInTheDocument()
    expect(within(items[1]).getByText("channels")).toBeInTheDocument()
    expect(within(items[1]).getByText("feed_silence_s")).toBeInTheDocument()
  })

  it("starts from defaults at version 0 when the config was never saved", async () => {
    server.use(http.get("*/api/v1/config", () => HttpResponse.json({ code: "not_found", message: "runtime config has not been initialized" }, { status: 404 })))
    const puts = capturePuts()
    renderWithQuery(<ConfigScreen />)

    expect(await screen.findByText(/never saved/)).toBeInTheDocument()
    expect(screen.getByLabelText("Stale entry (s)")).toHaveValue(30)
    expect(screen.getByLabelText("Symbol map from 1")).toHaveValue("SPX")
    expect(screen.getByLabelText("Symbol map to 1")).toHaveValue("SPXW")
    expect(screen.getByLabelText("Option tiers tier 1")).toHaveValue("default")

    await save("initial config") // nothing edited: version 0 still saves
    await screen.findByText("Version 1")
    expect(puts[0].expectedVersion).toBe(0)
    expect(puts[0].document).toEqual({ ...defaultDocument(), symbol_map: { SPX: "SPXW" } })
  })
})
