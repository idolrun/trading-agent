import { Screen } from "@/components/screen"
import { LotDetail } from "@/features/lots/lot-detail"

export default async function LotPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  return (
    <Screen title="Lot" back={{ href: "/", label: "Desk" }}>
      <LotDetail lotId={id} />
    </Screen>
  )
}
