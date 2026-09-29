import { Suspense } from "react"
import { EnrollScreen } from "@/features/auth/enroll-screen"

export default function EnrollPage() {
  return (
    <Suspense>
      <EnrollScreen />
    </Suspense>
  )
}
