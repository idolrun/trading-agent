import { Screen } from "@/components/screen"
import { UserManager } from "@/features/users/user-manager"

export default function UsersPage() {
  return (
    <Screen title="Users">
      <UserManager />
    </Screen>
  )
}
