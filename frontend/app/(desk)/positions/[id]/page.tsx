import { Screen } from "@/components/screen"
import { PositionDetail } from "@/features/positions/position-detail"

export default async function PositionPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  return (
    <Screen title="Position" back={{ href: "/positions", label: "Positions" }}>
      <PositionDetail positionId={id} />
    </Screen>
  )
}
