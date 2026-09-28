import { getFeedHealth } from "@/app/api/v1/_store"

export async function GET() {
  return Response.json(getFeedHealth())
}
