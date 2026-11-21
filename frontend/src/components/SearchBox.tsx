import { useEffect, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useSuggestions } from '../api/discovery'
import { Icon } from '../ui/icons'

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), ms)
    return () => clearTimeout(id)
  }, [value, ms])
  return debounced
}

/**
 * The search field with suggestions completed from listing titles. Arrow keys move through
 * them, Enter searches the highlighted one (or what was typed), Escape closes the list.
 */
export function SearchBox({
  id,
  value,
  onChange,
  onSearch,
  className = '',
}: {
  id: string
  value: string
  onChange: (value: string) => void
  onSearch: (q: string) => void
  className?: string
}) {
  const { t } = useTranslation()
  const listId = `${useId()}-suggestions`
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const typed = useDebounced(value.trim(), 150)
  const suggestions = useSuggestions(typed, open)
  const options = open && typed.length >= 2 ? (suggestions.data?.queries ?? []) : []
  const shown = options.length > 0

  function search(q: string) {
    setOpen(false)
    setActive(-1)
    onSearch(q)
  }

  return (
    <form
      role="search"
      className={`relative ${className}`}
      onSubmit={(e) => {
        e.preventDefault()
        search(active >= 0 && options[active] ? options[active] : value.trim())
      }}
    >
      <label htmlFor={id} className="sr-only">
        {t('nav.searchLabel')}
      </label>
      <Icon name="search" size={18} className="pointer-events-none absolute top-1/2 left-4 -translate-y-1/2 text-muted" />
      <input
        id={id}
        className="input h-11 rounded-full border-transparent pl-11 shadow-[var(--shadow-card)] focus:border-primary"
        type="search"
        enterKeyHint="search"
        maxLength={100}
        role="combobox"
        aria-expanded={shown}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={shown && active >= 0 ? `${listId}-${active}` : undefined}
        autoComplete="off"
        placeholder={t('nav.searchPlaceholder')}
        value={value}
        onChange={(e) => {
          onChange(e.target.value)
          setOpen(true)
          setActive(-1)
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown' && shown) {
            e.preventDefault()
            setActive((i) => Math.min(i + 1, options.length - 1))
          } else if (e.key === 'ArrowUp' && shown) {
            e.preventDefault()
            setActive((i) => Math.max(i - 1, -1))
          } else if (e.key === 'Escape') {
            setOpen(false)
            setActive(-1)
          }
        }}
      />
      {shown && (
        <ul id={listId} role="listbox" aria-label={t('search.suggestions')} className="card absolute z-30 mt-1 w-full overflow-hidden py-1">
          {options.map((option, i) => (
            <li
              key={option}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={i === active}
              onMouseDown={(e) => {
                e.preventDefault()
                onChange(option)
                search(option)
              }}
              className={`flex cursor-pointer items-center gap-2 px-4 py-2.5 text-sm ${i === active ? 'bg-surface-2' : 'hover:bg-surface-2'}`}
            >
              <Icon name="search" size={14} className="shrink-0 text-muted" />
              <span className="truncate">{option}</span>
            </li>
          ))}
        </ul>
      )}
    </form>
  )
}
