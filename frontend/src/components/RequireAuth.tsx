import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useMe } from '../api/auth'
import { Spinner } from '../ui/primitives'

/** Renders children only for signed-in users; others go to the sign-in page and come back. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { data: me, isPending } = useMe()
  const location = useLocation()
  if (isPending) {
    return (
      <div className="flex justify-center py-16 text-primary">
        <Spinner />
      </div>
    )
  }
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
  return children
}
