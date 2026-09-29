"use client"

import { AlertList } from "@/features/alerts/alert-list"
import { CardInbox } from "@/features/cards/card-inbox"
import { BookPanel } from "@/features/desk/book-panel"
import { FeedStatus } from "@/features/health/feed-status"
import { OrderList } from "@/features/orders/order-list"

export function DeskScreen() {
  return (
    <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 px-4 py-6 sm:px-6">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Inbox</h1>
          <p className="text-sm text-muted-foreground">
            Proposed trades waiting on a decision.
          </p>
        </div>
        <FeedStatus />
      </header>
      <div className="grid items-start gap-8 lg:grid-cols-[minmax(0,1.5fr)_minmax(18rem,1fr)]">
        <CardInbox />
        <div className="flex flex-col gap-8">
          <OrderList compact />
          <AlertList compact />
          <BookPanel />
        </div>
      </div>
    </main>
  )
}
