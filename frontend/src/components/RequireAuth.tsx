import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useMe } from '../api/auth'

/** Renders children only for signed-in users; others go to the sign-in page and come back. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { data: me, isPending } = useMe()
  const location = useLocation()
  if (isPending) return <p className="p-8 text-center text-stone-500">Loading…</p>
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname }} />
  return children
}
