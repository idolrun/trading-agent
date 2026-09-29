import { CardDetailScreen } from "@/features/cards/card-detail-screen"

export default async function CardPage({
  params,
}: {
  params: Promise<{ cardId: string }>
}) {
  const { cardId } = await params
  return <CardDetailScreen cardId={cardId} />
}
