import { apiGet, apiPost } from "@/lib/api/client"
import type { Schemas } from "@/lib/api/types"

export type Session = Schemas["SessionResponse"]

// Sign-in, step-up and change-password 401s mean "wrong password", not "session expired".
const credentials = { notifyUnauthorized: false }

export const fetchSession = () => apiGet<Session>("/api/v1/auth/session")

export const logout = () => apiPost<void>("/api/v1/auth/logout")

export const login = (email: string, password: string) =>
  apiPost<Session>("/api/v1/auth/login", { email, password }, credentials)

export const stepUp = (password: string) =>
  apiPost<Schemas["StepUpResponse"]>("/api/v1/auth/step-up", { password }, credentials)

export const changePassword = (currentPassword: string, newPassword: string) =>
  apiPost<void>("/api/v1/auth/change-password", { currentPassword, newPassword }, credentials)
