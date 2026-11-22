import { useSyncExternalStore } from 'react'

/**
 * Whether some request is waiting to be sent again because the API did not answer (starting or
 * unreachable). The app shell shows a notice while this is true, so a long wait is explained.
 */
let waiting = 0
const listeners = new Set<() => void>()

const emit = () => listeners.forEach((listener) => listener())

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** Marks one request as waiting; the returned function ends the wait (safe to call twice). */
export function beginWaiting(): () => void {
  waiting += 1
  emit()
  let ended = false
  return () => {
    if (ended) return
    ended = true
    waiting -= 1
    emit()
  }
}

export function useApiWaiting(): boolean {
  return useSyncExternalStore(subscribe, () => waiting > 0)
}
