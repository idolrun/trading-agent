import { Toaster } from "@/components/toaster"
import { RequireSession } from "@/features/auth/require-session"
import { DeskHeader } from "@/features/desk/desk-header"

export default function DeskLayout({ children }: LayoutProps<"/">) {
  return (
    <RequireSession header={<DeskHeader />}>
      {children}
      <Toaster />
    </RequireSession>
  )
}
