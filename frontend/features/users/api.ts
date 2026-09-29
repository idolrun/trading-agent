import {
  infiniteQueryOptions,
  queryOptions,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { apiGet, apiPost } from "@/lib/api/client"
import type { ApiError } from "@/lib/api/errors"
import { domainKeys } from "@/lib/query/keys"
import type { ListFilters, Schemas } from "@/lib/api/types"

export type User = Schemas["UserOut"]
export type UserWithPassword = Schemas["UserWithPassword"]
export type AuditEntry = Schemas["AuditEntry"]
export type AuditAction = Schemas["AuditAction"]
export type AuditFilters = ListFilters<"audit_log_api_v1_audit_get">

export const userKeys = domainKeys("users")
export const auditKeys = domainKeys("audit")

const usersQuery = queryOptions({
  queryKey: userKeys.list({}),
  queryFn: () => apiGet<User[]>("/api/v1/users"),
})

export const useUsers = () => useQuery(usersQuery)

const userAction = (id: string, action: "deactivate" | "reactivate" | "reset-password") =>
  apiPost<User | UserWithPassword>(`/api/v1/users/${encodeURIComponent(id)}/${action}`)

export function useUserMutations() {
  const queryClient = useQueryClient()
  const settle = () => {
    void queryClient.invalidateQueries({ queryKey: userKeys.all })
    void queryClient.invalidateQueries({ queryKey: auditKeys.all })
  }
  return {
    add: useMutation<UserWithPassword, ApiError, string>({
      mutationFn: (email) => apiPost<UserWithPassword>("/api/v1/users", { email }),
      onSettled: settle,
    }),
    setActive: useMutation<User, ApiError, { id: string; active: boolean }>({
      mutationFn: ({ id, active }) =>
        userAction(id, active ? "reactivate" : "deactivate") as Promise<User>,
      onSettled: settle,
    }),
    resetPassword: useMutation<UserWithPassword, ApiError, string>({
      mutationFn: (id) => userAction(id, "reset-password") as Promise<UserWithPassword>,
      onSettled: settle,
    }),
  }
}

export function useAuditLog(filters: AuditFilters) {
  const query = infiniteQueryOptions({
    queryKey: auditKeys.list(filters),
    queryFn: ({ pageParam }) =>
      apiGet<Schemas["Page_AuditEntry_"]>("/api/v1/audit", { ...filters, cursor: pageParam, limit: 50 }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.nextCursor ?? undefined,
  })
  return useInfiniteQuery({ ...query, select: (data) => ({ items: data.pages.flatMap((page) => page.items) }) })
}
