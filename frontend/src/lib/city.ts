import { useCallback, useState, useSyncExternalStore } from 'react'
import { useMe } from '../api/auth'
import type { City } from '../components/CityPicker'

const KEY = 'tp_near'
const EVENT = 'tp-city-change'

let cachedRaw: string | null | undefined
let cached: City | null = null
// Private mode or blocked storage: the choice lasts for this visit only.
let memory: string | null = null

function readRaw(): string | null {
  try {
    return localStorage.getItem(KEY)
  } catch {
    return null
  }
}

/** The stored city, parsed once per change so React sees a stable value between changes. */
function snapshot(): City | null {
  const raw = readRaw() ?? memory
  if (raw !== cachedRaw) {
    cachedRaw = raw
    try {
      const parsed = raw ? (JSON.parse(raw) as City) : null
      cached = parsed && typeof parsed.ibge_code === 'number' ? parsed : null
    } catch {
      cached = null
    }
  }
  return cached
}

function subscribe(onChange: () => void): () => void {
  const onStorage = (e: StorageEvent) => e.key === KEY && onChange()
  window.addEventListener('storage', onStorage)
  window.addEventListener(EVENT, onChange)
  return () => {
    window.removeEventListener('storage', onStorage)
    window.removeEventListener(EVENT, onChange)
  }
}

/** The city chosen in this browser for nearby listings and shipping quotes. */
export function useChosenCity(): [City | null, (city: City | null) => void] {
  const city = useSyncExternalStore(subscribe, snapshot, () => null)
  const choose = useCallback((next: City | null) => {
    const raw = next ? JSON.stringify({ ibge_code: next.ibge_code, name: next.name, state: next.state }) : null
    memory = raw
    try {
      if (raw) localStorage.setItem(KEY, raw)
      else localStorage.removeItem(KEY)
    } catch {
      // Kept in memory instead.
    }
    window.dispatchEvent(new Event(EVENT))
  }, [])
  return [city, choose]
}

/** The city on the signed-in person's profile, if any. */
export function useProfileCity(): City | null {
  const { data: me } = useMe()
  const code = me?.municipality_id
  const name = me?.city
  const state = me?.state
  return code && name && state ? { ibge_code: code, name, state } : null
}

/** Where to ship and measure distances from: the city chosen here, else the profile's city. */
export function useShipToCity(): [City | null, (city: City | null) => void] {
  const [chosen, choose] = useChosenCity()
  const profile = useProfileCity()
  return [chosen ?? profile, choose]
}

/**
 * A city picker bound to the ship-to city. Typing over the current city empties the picker
 * without falling back to the profile's city (which would put it back in the box); ``city``
 * stays the last one chosen until another is picked.
 */
export function useShipToField(): {
  city: City | null
  value: City | null
  onChange: (city: City | null) => void
} {
  const [city, choose] = useShipToCity()
  const [typing, setTyping] = useState(false)
  const onChange = useCallback(
    (next: City | null) => {
      if (next) choose(next)
      setTyping(next === null)
    },
    [choose],
  )
  return { city, value: typing ? null : city, onChange }
}
