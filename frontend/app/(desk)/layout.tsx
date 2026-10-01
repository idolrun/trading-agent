import { Toaster } from "@/components/toaster"
import { RequireSession } from "@/features/auth/require-session"
import { DeskControls, DeskHeader } from "@/features/desk/desk-header"
import { DeskNav } from "@/features/desk/desk-nav"
import { SystemBanner } from "@/features/health/system-banner"

export default function DeskLayout({ children }: LayoutProps<"/">) {
  return (
    <RequireSession header={<DeskHeader />} sidebar={<DeskNav />} actions={<DeskControls />}>
      <SystemBanner />
      {children}
      <Toaster />
    </RequireSession>
  )
}
