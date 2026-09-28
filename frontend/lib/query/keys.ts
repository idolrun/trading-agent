/** Drop empty values and sort keys so equivalent filters share one cache entry. */
export function stableFilters<F extends object>(filters: F): F {
  const entries = Object.entries(filters)
    .filter(([, value]) => value !== undefined && value !== null && value !== "")
    .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
  return Object.fromEntries(entries) as F
}

/**
 * Domain keys are tuples: [domain, scope, params].
 * `lists()` and `details()` are prefixes, so a write can invalidate one scope
 * without refetching every query in the domain.
 */
export function domainKeys<const Domain extends string>(domain: Domain) {
  return {
    all: [domain] as const,
    lists: () => [domain, "list"] as const,
    list: <F extends object>(filters: F) =>
      [domain, "list", stableFilters(filters)] as const,
    details: () => [domain, "detail"] as const,
    detail: (id: string) => [domain, "detail", id] as const,
  }
}
