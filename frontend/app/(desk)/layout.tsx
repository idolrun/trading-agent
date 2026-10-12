import { Toaster } from "@/components/toaster"
import { RequireSession } from "@/features/auth/require-session"
import { DeskControls, DeskHeader, DeskMenu } from "@/features/desk/desk-header"
import { DeskNav } from "@/features/desk/desk-nav"
import { OperationalStatus } from "@/features/health/operational-status"

export default function DeskLayout({ children }: LayoutProps<"/">) {
  return (
    <RequireSession header={<DeskHeader />} sidebar={<DeskNav />} actions={<DeskControls />} menu={<DeskMenu />}>
      <OperationalStatus />
      {children}
      <Toaster />
    </RequireSession>
  )
}
