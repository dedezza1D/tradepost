const SIZES = { sm: 'h-9 w-9 text-sm', md: 'h-11 w-11 text-base', lg: 'h-16 w-16 text-2xl', xl: 'h-24 w-24 text-4xl' }

export function Avatar({ name, url, size = 'md' }: { name: string; url: string | null | undefined; size?: keyof typeof SIZES }) {
  const box = `${SIZES[size]} shrink-0 overflow-hidden rounded-full`
  if (url) return <img src={url} alt="" className={`${box} bg-surface-2 object-cover`} loading="lazy" decoding="async" />
  return (
    <span aria-hidden className={`${box} grid place-items-center bg-primary-soft font-bold text-primary`}>
      {name.slice(0, 1).toUpperCase()}
    </span>
  )
}
