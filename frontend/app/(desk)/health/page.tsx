import { Screen } from "@/components/screen"
import { HealthScreen } from "@/features/health/health-screen"

export default function HealthPage() {
  return (
    <Screen title="System health">
      <HealthScreen />
    </Screen>
  )
}
