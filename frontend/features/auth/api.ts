import type {
  AuthenticationResponseJSON,
  PublicKeyCredentialCreationOptionsJSON,
  PublicKeyCredentialRequestOptionsJSON,
  RegistrationResponseJSON,
} from "@simplewebauthn/browser"
import { apiGet, apiPost } from "@/lib/api/client"
import { isApiError } from "@/lib/api/errors"
import type { Schemas } from "@/lib/api/types"

export type Session = Schemas["SessionResponse"]

// Sign-in and step-up 401s mean "passkey rejected", not "session expired".
const ceremony = { notifyUnauthorized: false }

export const fetchSession = () => apiGet<Session>("/api/v1/auth/session")

export const logout = () => apiPost<void>("/api/v1/auth/logout")

// Options endpoints return raw WebAuthn JSON, not a schema-typed body.
export const loginOptions = () =>
  apiPost<PublicKeyCredentialRequestOptionsJSON>("/api/v1/auth/login/options", undefined, ceremony)

export const loginVerify = (credential: AuthenticationResponseJSON) =>
  apiPost<Session>("/api/v1/auth/login/verify", { credential }, ceremony)

export const stepUpOptions = () =>
  apiPost<PublicKeyCredentialRequestOptionsJSON>("/api/v1/auth/step-up/options", undefined, ceremony)

export const stepUpVerify = (credential: AuthenticationResponseJSON) =>
  apiPost<Schemas["StepUpResponse"]>("/api/v1/auth/step-up/verify", { credential }, ceremony)

export const registerOptions = (enrollmentToken: string, label: string) =>
  apiPost<PublicKeyCredentialCreationOptionsJSON>(
    "/api/v1/auth/register/options",
    { enrollmentToken, label },
    ceremony,
  )

export const registerVerify = (enrollmentToken: string, credential: RegistrationResponseJSON) =>
  apiPost<Schemas["RegisterVerifyResponse"]>(
    "/api/v1/auth/register/verify",
    { enrollmentToken, credential },
    ceremony,
  )

export function webAuthnSupported(): boolean {
  return typeof window !== "undefined" && "PublicKeyCredential" in window
}

export type CeremonyOutcome = { kind: "cancelled" | "error"; message: string }

/** Maps a failed ceremony to UI copy. A dismissed prompt is not an error. */
export function describeCeremonyError(error: unknown): CeremonyOutcome {
  if (error instanceof Error && error.name === "NotAllowedError") {
    return { kind: "cancelled", message: "Passkey prompt closed. Nothing was changed." }
  }
  if (isApiError(error)) return { kind: "error", message: error.message }
  return { kind: "error", message: "Something went wrong with your passkey. Try again." }
}
