import { Toaster } from "@/components/toaster"
import { RequireSession } from "@/features/auth/require-session"
import { DeskHeader } from "@/features/desk/desk-header"
import { SystemBanner } from "@/features/health/system-banner"

export default function DeskLayout({ children }: LayoutProps<"/">) {
  return (
    <RequireSession header={<DeskHeader />}>
      <SystemBanner />
      {children}
      <Toaster />
    </RequireSession>
  )
}
