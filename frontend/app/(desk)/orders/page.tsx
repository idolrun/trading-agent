import { Screen } from "@/components/screen"
import { OrderList } from "@/features/orders/order-list"

export default function OrdersPage() {
  return (
    <Screen title="Orders">
      <OrderList />
    </Screen>
  )
}
