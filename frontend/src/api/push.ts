import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, json } from './client'

export const CATEGORIES = ['messages', 'offers', 'orders', 'saved', 'activity'] as const
export type Category = (typeof CATEGORIES)[number]

export interface CategoryPreference {
  category: Category
  push: boolean
  email: boolean
  /** Only order and dispute notices have e-mails. */
  email_possible: boolean
}

export interface NotificationPreferences {
  push_available: boolean
  email_available: boolean
  public_key: string | null
  devices: number
  categories: CategoryPreference[]
}

const KEY = ['notification-preferences']
// The account that turned push on in this browser. Another person signing in here later does
// not inherit it: their account is only attached when they turn push on themselves.
const OWNER = 'tp_push_owner'

export function useNotificationPreferences() {
  return useQuery({ queryKey: KEY, queryFn: () => api<NotificationPreferences>('/api/me/notification-preferences') })
}

export function useSetPreference() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (pref: Pick<CategoryPreference, 'category' | 'push' | 'email'>) =>
      api<NotificationPreferences>('/api/me/notification-preferences', { method: 'PUT', body: json(pref) }),
    onSuccess: (data) => qc.setQueryData(KEY, data),
  })
}

export type PushSupport = 'supported' | 'unsupported' | 'needs-install'

export function pushSupport(): PushSupport {
  if (typeof window === 'undefined') return 'unsupported'
  const ios = /iPad|iPhone|iPod/.test(navigator.userAgent)
  const standalone = window.matchMedia?.('(display-mode: standalone)').matches
  // On iPhone and iPad, web push works only for a site added to the home screen.
  if (ios && !standalone) return 'needs-install'
  if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) return 'unsupported'
  return 'supported'
}

function keyBytes(base64url: string): Uint8Array<ArrayBuffer> {
  const padded = (base64url + '='.repeat((4 - (base64url.length % 4)) % 4)).replace(/-/g, '+').replace(/_/g, '/')
  const raw = atob(padded)
  const bytes = new Uint8Array(new ArrayBuffer(raw.length))
  for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i)
  return bytes
}

async function registration(): Promise<ServiceWorkerRegistration | null> {
  if (pushSupport() !== 'supported') return null
  // In development there is no service worker; `ready` would never settle.
  const existing = await navigator.serviceWorker.getRegistration()
  return existing ? navigator.serviceWorker.ready : null
}

export async function currentSubscription(): Promise<PushSubscription | null> {
  const reg = await registration()
  return reg ? reg.pushManager.getSubscription() : null
}

function readOwner(): string | null {
  try {
    return localStorage.getItem(OWNER)
  } catch {
    return null
  }
}

function writeOwner(userId: string | null) {
  try {
    if (userId) localStorage.setItem(OWNER, userId)
    else localStorage.removeItem(OWNER)
  } catch {
    // Without storage the device is simply not re-attached on the next visit.
  }
}

/** Ask the browser for permission, subscribe and register this device for the account. */
export async function enablePush(publicKey: string, userId: string): Promise<'enabled' | 'denied' | 'unavailable'> {
  const reg = await registration()
  if (!reg) return 'unavailable'
  const permission = await Notification.requestPermission()
  if (permission !== 'granted') return 'denied'
  const subscription =
    (await reg.pushManager.getSubscription()) ??
    (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(publicKey) }))
  await api<void>('/api/me/push-subscriptions', { method: 'POST', body: json(subscription.toJSON()) })
  writeOwner(userId)
  return 'enabled'
}

/** Stop pushes to this browser, for this account and any other. */
export async function disablePush(): Promise<void> {
  const subscription = await currentSubscription()
  writeOwner(null)
  if (!subscription) return
  try {
    await api<void>('/api/me/push-subscriptions/remove', { method: 'POST', body: json({ endpoint: subscription.endpoint }) })
  } finally {
    await subscription.unsubscribe()
  }
}

/**
 * On each visit, tie this browser's subscription to the current session again (a device only
 * receives while the session that registered it is signed in). Only for the account that
 * turned push on here.
 */
export async function refreshPush(userId: string): Promise<void> {
  if (readOwner() !== userId || Notification.permission !== 'granted') return
  const subscription = await currentSubscription()
  if (!subscription) return
  await api<void>('/api/me/push-subscriptions', { method: 'POST', body: json(subscription.toJSON()) })
}

export async function sendTestPush(): Promise<{ sent: number; failed: number }> {
  return api<{ sent: number; failed: number }>('/api/me/push-subscriptions/test', { method: 'POST' })
}
