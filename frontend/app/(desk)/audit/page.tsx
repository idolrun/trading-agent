import { Screen } from "@/components/screen"
import { AuditLog } from "@/features/users/audit-log"

export default function AuditPage() {
  return (
    <Screen title="Audit log">
      <AuditLog />
    </Screen>
  )
}
