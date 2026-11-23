import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, api, json } from './client'
import type { Me } from './types'
import { disablePush } from './push'

export function useMe() {
  return useQuery({
    queryKey: ['me'],
    queryFn: async () => {
      try {
        return await api<Me>('/api/auth/me')
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null
        throw error
      }
    },
    staleTime: 60_000,
  })
}

export function useLogin() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { email: string; password: string }) =>
      api<Me>('/api/auth/login', { method: 'POST', body: json(body) }),
    onSuccess: (me) => qc.setQueryData(['me'], me),
  })
}

export function useRegister() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: {
      email: string
      password: string
      display_name: string
      city?: string
      state?: string
      locale?: 'pt-BR' | 'en'
    }) => api<Me>('/api/auth/register', { method: 'POST', body: json(body) }),
    onSuccess: (me) => qc.setQueryData(['me'], me),
  })
}

export function useLogout() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      // This browser stops receiving this account's notices before the session ends.
      await disablePush().catch(() => undefined)
      await api<void>('/api/auth/logout', { method: 'POST' })
    },
    onSuccess: () => {
      qc.setQueryData(['me'], null)
      qc.removeQueries({ predicate: (q) => q.queryKey[0] !== 'me' })
    },
  })
}

/** Asks for a reset link. The answer is the same whether or not the address has an account. */
export function useRequestPasswordReset() {
  return useMutation({
    mutationFn: (body: { email: string }) =>
      api<void>('/api/auth/password-reset', { method: 'POST', body: json(body) }),
  })
}

/** Sets a new password with the link's token. Every session ends, so the person signs in again. */
export function useResetPassword() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { token: string; new_password: string }) =>
      api<void>('/api/auth/password-reset/confirm', { method: 'POST', body: json(body) }),
    onSuccess: () => {
      qc.setQueryData(['me'], null)
      qc.removeQueries({ predicate: (q) => q.queryKey[0] !== 'me' })
    },
  })
}
