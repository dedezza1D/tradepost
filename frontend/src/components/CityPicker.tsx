import { useQuery } from '@tanstack/react-query'
import { useEffect, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../api/client'
import { errorMessage } from '../lib/errors'

export interface City {
  ibge_code: number
  name: string
  state: string
}

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), ms)
    return () => clearTimeout(id)
  }, [value, ms])
  return debounced
}

/**
 * Choose a Brazilian city by typing its name (accent-insensitive) or by postal code. Only cities
 * from the official list can be chosen, so what is stored always resolves on the server.
 */
export function CityPicker({
  value,
  onChange,
  required = false,
  allowCep = true,
  label,
}: {
  value: City | null
  onChange: (city: City | null) => void
  required?: boolean
  allowCep?: boolean
  label?: string
}) {
  const { t } = useTranslation()
  const id = useId()
  const [text, setText] = useState(value ? `${value.name} - ${value.state}` : '')
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const [cep, setCep] = useState('')
  const [cepError, setCepError] = useState<string | null>(null)
  const query = useDebounced(text.split(' - ')[0].trim(), 200)

  useEffect(() => {
    setText(value ? `${value.name} - ${value.state}` : '')
  }, [value])

  const results = useQuery({
    queryKey: ['cities', query],
    queryFn: () => api<City[]>(`/api/geo/cities?q=${encodeURIComponent(query)}`),
    enabled: open && query.length >= 2,
    staleTime: 60_000,
  })
  const options = results.data ?? []

  function choose(city: City) {
    onChange(city)
    setOpen(false)
  }

  async function lookupCep() {
    setCepError(null)
    try {
      choose(await api<City>(`/api/geo/cep/${cep.replace(/\D/g, '')}`))
    } catch (error) {
      setCepError(errorMessage(error, t))
    }
  }

  return (
    <div className="space-y-2">
      <div className="relative">
        <label className="label" htmlFor={id}>
          {label ?? t('city.label')}
        </label>
        <input
          id={id}
          role="combobox"
          aria-expanded={open && options.length > 0}
          aria-controls={`${id}-list`}
          aria-autocomplete="list"
          autoComplete="off"
          className="input"
          placeholder={t('city.placeholder')}
          required={required && !value}
          value={text}
          onChange={(e) => {
            setText(e.target.value)
            setOpen(true)
            setActive(0)
            if (value) onChange(null)
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') setActive((i) => Math.min(i + 1, options.length - 1))
            else if (e.key === 'ArrowUp') setActive((i) => Math.max(i - 1, 0))
            else if (e.key === 'Enter' && open && options[active]) {
              e.preventDefault()
              choose(options[active])
            }
          }}
        />
        {open && query.length >= 2 && (
          <ul id={`${id}-list`} role="listbox" className="card absolute z-20 mt-1 max-h-60 w-full overflow-y-auto py-1">
            {results.isSuccess && options.length === 0 && (
              <li className="px-3 py-2 text-sm text-muted">{t('city.noResults')}</li>
            )}
            {options.map((city, i) => (
              <li
                key={city.ibge_code}
                role="option"
                aria-selected={i === active}
                onMouseDown={(e) => {
                  e.preventDefault()
                  choose(city)
                }}
                className={`cursor-pointer px-3 py-2 text-sm ${i === active ? 'bg-primary-soft' : ''}`}
              >
                {city.name} <span className="text-muted">- {city.state}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
      {allowCep && (
        <div className="flex items-end gap-2">
          <div className="flex-1">
            <label className="label" htmlFor={`${id}-cep`}>
              {t('city.cep')}
            </label>
            <input
              id={`${id}-cep`}
              className="input"
              inputMode="numeric"
              maxLength={9}
              placeholder={t('city.cepPlaceholder')}
              value={cep}
              onChange={(e) => setCep(e.target.value)}
            />
          </div>
          <button
            type="button"
            className="btn-secondary"
            disabled={cep.replace(/\D/g, '').length !== 8}
            onClick={() => void lookupCep()}
          >
            {t('city.useCep')}
          </button>
        </div>
      )}
      {cepError && <p className="text-xs text-danger">{cepError}</p>}
    </div>
  )
}
