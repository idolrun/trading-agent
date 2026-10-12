import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { http, HttpResponse } from "msw"
import { describe, expect, it } from "vitest"
import { ConfigScreen } from "@/features/config/config-screen"
import { HELP } from "@/features/config/help"
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
  const confirm = screen.queryByRole("button", { name: "Confirm and save" })
  if (confirm) await userEvent.click(confirm)
}

describe("ConfigScreen", { timeout: 20_000 }, () => {
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
      expect(input).toHaveAccessibleDescription("not set: entries disabled")
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
    await save("tighten the local fallback")

    await screen.findByText(/Version 4/)
    expect(puts[0].document).toMatchObject({
      laya: { enabled: false, timeout_ms: 10000, t_chat: "0.95", t_field: "0.9", t_type: "0.95", t_link: "0.9" }, // on by default: the click turns it off
    })
  })

  it("saves the full document with expectedVersion and reason", async () => {
    const puts = capturePuts()
    renderWithQuery(<ConfigScreen />)
    await screen.findByLabelText("Max daily loss (USD)")

    expect(screen.queryByRole("button", { name: "Save config" })).not.toBeInTheDocument() // nothing edited
    await userEvent.type(screen.getByLabelText("Max daily loss (USD)"), "500")
    expect(saveButton()).toBeDisabled() // no reason yet
    await save("set risk limits")

    await screen.findByText(/Version 4/)
    expect(puts).toHaveLength(1)
    expect(puts[0].expectedVersion).toBe(3)
    expect(puts[0].reason).toBe("set risk limits")
    expect(Object.keys(puts[0].document).sort()).toEqual(Object.keys(configFixture.document).sort())
    expect(puts[0].document).toMatchObject({
      max_daily_loss_usd: "500",
      channels: ["999888777666555444"],
      feed_silence_s: 60,
      jev: { model: "jev-1.13.0", timeout_ms: 700, queue_timeout_ms: 250, budget_ms: 1200, enabled: true },
    })
    // dirty state reset: save bar gone, nothing to save
    expect(screen.queryByLabelText("Reason for this change")).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Save config" })).not.toBeInTheDocument()
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
    await screen.findByText(/Version 4/)

    await userEvent.type(screen.getByLabelText("Reason for this change"), "retry")
    await userEvent.click(saveButton())
    await userEvent.click(await screen.findByRole("button", { name: "Confirm and save" })) // max_daily_loss_usd is high impact
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

  it("renders the change history as expandable versions", async () => {
    renderWithQuery(<ConfigScreen />)
    const history = await screen.findByRole("heading", { name: "History" })
    const items = await within(history.parentElement!).findAllByRole("listitem")
    expect(items).toHaveLength(2)
    expect(within(items[0]).getByText("Version 3")).toBeInTheDocument()
    expect(within(items[0]).getByText("alice")).toBeInTheDocument()
    expect(within(items[0]).getByText("tighten chase")).toBeInTheDocument()
    expect(within(items[0]).getByText(/1 setting changed/)).toBeInTheDocument()
    expect(within(items[0]).getByText("entry_chase_pct")).toBeInTheDocument()
    expect(within(items[0]).getByText(/"3"/)).toBeInTheDocument() // before
    expect(within(items[1]).getByText(/2 settings changed/)).toBeInTheDocument()
    expect(within(items[1]).getByText("channels")).toBeInTheDocument()
    expect(within(items[1]).getByText("feed_silence_s")).toBeInTheDocument()
  })

  it("shows the version and last update in the header", async () => {
    renderWithQuery(<ConfigScreen />)
    expect((await screen.findAllByText(/Version 3/)).length).toBeGreaterThan(0)
    expect(await screen.findByText(/by alice/)).toBeInTheDocument()
    expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument()
    await userEvent.type(screen.getByLabelText("Max daily loss (USD)"), "5")
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument()
  })

  it("has a help button on every control, with the five kinds of help", async () => {
    renderWithQuery(<ConfigScreen />)
    await screen.findByLabelText("Max daily loss (USD)")
    const names = Object.keys(HELP)
    // every entry says what it means, what it affects and gives an example
    for (const name of names) {
      expect(HELP[name].means.length).toBeGreaterThan(10)
      expect(HELP[name].affects.length).toBeGreaterThan(10)
      expect(HELP[name].example.length).toBeGreaterThan(3)
    }
    // one help button per documented control (the document has no control without help)
    expect(screen.getAllByRole("button", { name: /^Help: / })).toHaveLength(names.length)
    for (const name of ["max_total_exposure_usd", "catch_up_backlog_n"]) expect(HELP[name].risk).toBeTruthy()
  })

  it("opens help on click and closes it with Escape", async () => {
    renderWithQuery(<ConfigScreen />)
    await userEvent.click(await screen.findByRole("button", { name: "Help: Feed silence alert (s)" }))
    expect(await screen.findByText("What it affects")).toBeInTheDocument()
    expect(screen.getByText(/600 = 10 minutes/)).toBeInTheDocument()
    await userEvent.keyboard("{Escape}")
    await waitFor(() => expect(screen.queryByText("What it affects")).not.toBeInTheDocument())
  })

  it("explains an invalid value next to the field and blocks saving", async () => {
    const puts = capturePuts()
    renderWithQuery(<ConfigScreen />)
    const chase = await screen.findByLabelText("Chase limit (%)")
    await userEvent.type(chase, "150")
    expect(chase).toHaveAttribute("aria-invalid", "true")
    expect(chase).toHaveAccessibleDescription(/at most 100/)
    await userEvent.type(screen.getByLabelText("Reason for this change"), "x")
    expect(saveButton()).toBeDisabled()
    expect(puts).toHaveLength(0)
  })

  it("asks to confirm a high-impact change and shows before and after", async () => {
    const puts = capturePuts()
    renderWithQuery(<ConfigScreen />)
    await userEvent.type(await screen.findByLabelText("Max daily loss (USD)"), "500")
    await userEvent.type(screen.getByLabelText("Reason for this change"), "limit")
    await userEvent.click(saveButton())
    const list = await screen.findByRole("list", { name: "High-impact changes" })
    expect(within(list).getByText("max_daily_loss_usd")).toBeInTheDocument()
    expect(puts).toHaveLength(0) // nothing sent before confirming
    await userEvent.click(screen.getByRole("button", { name: "Confirm and save" }))
    await waitFor(() => expect(puts).toHaveLength(1))
  })

  it("discards edits back to the saved version", async () => {
    renderWithQuery(<ConfigScreen />)
    await userEvent.type(await screen.findByLabelText("Max daily loss (USD)"), "500")
    await userEvent.click(screen.getByRole("button", { name: "Discard changes" }))
    expect(screen.getByLabelText("Max daily loss (USD)")).toHaveValue(null)
    expect(screen.queryByRole("button", { name: "Save config" })).not.toBeInTheDocument()
  })

  it("starts from defaults at version 0 when the config was never saved", async () => {
    server.use(http.get("*/api/v1/config", () => HttpResponse.json({ code: "not_found", message: "runtime config has not been initialized" }, { status: 404 })))
    const puts = capturePuts()
    renderWithQuery(<ConfigScreen />)

    expect(await screen.findByText(/never saved/)).toBeInTheDocument()
    expect(screen.getByLabelText("Stale entry (s)")).toHaveValue(30)
    expect(screen.getByLabelText("Symbol map from 1")).toHaveValue("SPX")
    expect(screen.getByLabelText("Symbol map to 1")).toHaveValue("SPXW")
    expect(screen.queryByLabelText("Option tiers tier 1")).not.toBeInTheDocument() // C105: no sizing tiers

    await save("initial config") // nothing edited: version 0 still saves
    await screen.findByText(/Version 1/)
    expect(puts[0].expectedVersion).toBe(0)
    expect(puts[0].document).toEqual({ ...defaultDocument(), symbol_map: { SPX: "SPXW" } })
  })
})
