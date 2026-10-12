import { deskFrame } from "@/components/screen"
import { AlertList } from "@/features/alerts/alert-list"
import { CardInbox } from "@/features/cards/card-inbox"
import { BookPanel } from "@/features/desk/book-panel"
import { OrderList } from "@/features/orders/order-list"

/** The inbox is the workspace; orders, alerts and the book sit beside it on wide screens, below it on narrow ones. */
export function DeskScreen() {
  return (
    <div className={deskFrame}>
      <h1 className="sr-only">Inbox</h1>
      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1.7fr)_minmax(17rem,1fr)]">
        <CardInbox />
        <aside aria-label="Orders and alerts" className="flex flex-col gap-5">
          <AlertList compact />
          <OrderList compact />
          <BookPanel />
        </aside>
      </div>
    </div>
  )
}
