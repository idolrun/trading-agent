import type { components, operations } from "@/lib/api/schema"

/** Generated contract types. Never hand-write a response shape. */
export type Schemas = components["schemas"]

/** Query-string parameters of an operation, minus pagination. */
export type ListFilters<Op extends keyof operations> = Omit<
  NonNullable<operations[Op]["parameters"]["query"]>,
  "cursor" | "limit"
>
