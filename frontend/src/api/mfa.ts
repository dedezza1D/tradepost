import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, json } from './client'
import type { Me } from './types'

export interface MfaStatus {
  enabled: boolean
  recovery_codes_left: number
}

export interface MfaSetup {
  /** Base32 key, for typing into the app when the QR code cannot be scanned. */
  secret: string
  otpauth_uri: string
  /** QR code as an SVG data URI, shown in an <img>. */
  qr_code: string
}

export interface SecondFactor {
  code?: string
  recovery_code?: string
}

interface RecoveryCodes {
  recovery_codes: string[]
}

export function useMfaStatus() {
  return useQuery({ queryKey: ['mfa'], queryFn: () => api<MfaStatus>('/api/auth/mfa') })
}

/** Second step of signing in, after the password, for accounts with two-step verification. */
export function useLoginSecondFactor() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: SecondFactor) => api<Me>('/api/auth/login/mfa', { method: 'POST', body: json(body) }),
    onSuccess: (me) => qc.setQueryData(['me'], me),
  })
}

export function useMfaSetup() {
  return useMutation({
    mutationFn: (password: string) => api<MfaSetup>('/api/auth/mfa/setup', { method: 'POST', body: json({ password }) }),
  })
}

export function useMfaConfirm() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (code: string) => api<RecoveryCodes>('/api/auth/mfa/confirm', { method: 'POST', body: json({ code }) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['mfa'] }),
  })
}

export function useMfaDisable() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: SecondFactor & { password: string }) =>
      api<void>('/api/auth/mfa/disable', { method: 'POST', body: json(body) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['mfa'] }),
  })
}

export function useNewRecoveryCodes() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (password: string) =>
      api<RecoveryCodes>('/api/auth/mfa/recovery-codes', { method: 'POST', body: json({ password }) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['mfa'] }),
  })
}
