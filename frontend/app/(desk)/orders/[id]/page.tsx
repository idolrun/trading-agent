import { Screen } from "@/components/screen"
import { OrderDetail } from "@/features/orders/order-detail"

export default async function OrderPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  return (
    <Screen title="Order" back={{ href: "/orders", label: "Orders" }}>
      <OrderDetail orderId={id} />
    </Screen>
  )
}
