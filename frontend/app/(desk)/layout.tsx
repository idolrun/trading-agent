import { RequireSession } from "@/features/auth/require-session"

export default function DeskLayout({ children }: LayoutProps<"/">) {
  return <RequireSession>{children}</RequireSession>
}
