import { queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useRouter } from "next/navigation"
import { fetchSession, logout } from "@/features/auth/api"

export const sessionKey = ["auth", "session"] as const

export const sessionQuery = queryOptions({
  queryKey: sessionKey,
  queryFn: fetchSession,
  staleTime: 60_000,
})

export function useSession() {
  return useQuery(sessionQuery)
}

/** `next` is user-controlled: accept same-origin absolute paths only. */
export function safeNext(next: string | null | undefined): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) return "/"
  return /^\/(login|enroll)(?:[/?#]|$)/.test(next) ? "/" : next
}

export function loginUrl(next?: string): string {
  const target = safeNext(next)
  return target === "/" ? "/login" : `/login?next=${encodeURIComponent(target)}`
}

export function useLogout() {
  const queryClient = useQueryClient()
  const router = useRouter()
  return useMutation({
    mutationFn: logout,
    onSuccess: () => {
      queryClient.clear()
      router.replace("/login")
    },
  })
}
