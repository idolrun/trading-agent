import { QueryClientProvider } from "@tanstack/react-query"
import { render } from "@testing-library/react"
import type { ReactElement } from "react"
import { makeQueryClient } from "@/lib/query/get-query-client"

/** Fresh QueryClient per test; retries off so error states surface at once. */
export function renderWithQuery(ui: ReactElement) {
  const client = makeQueryClient()
  client.setDefaultOptions({ queries: { retry: false } })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}
