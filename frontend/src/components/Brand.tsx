import { Link } from 'react-router-dom'

/** The mark: a price tag on a red tile. `inverse` puts a red tag on a white tile (on the band). */
export function BrandMark({ size = 32, inverse = false }: { size?: number; inverse?: boolean }) {
  const tile = inverse ? '#ffffff' : 'url(#tp-mark)'
  const tag = inverse ? '#c8102e' : '#ffffff'
  const hole = inverse ? '#ffffff' : '#c3102b'
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden className="shrink-0">
      <defs>
        <linearGradient id="tp-mark" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#e1252f" />
          <stop offset="1" stopColor="#c3102b" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="8" fill={tile} />
      <path
        d="M15.3 6.6a2 2 0 0 0-1.4-.6H8a2 2 0 0 0-2 2v5.9a2 2 0 0 0 .6 1.4l8.9 8.9a2.4 2.4 0 0 0 3.4 0l5.3-5.3a2.4 2.4 0 0 0 0-3.4z"
        fill={tag}
      />
      <circle cx="10.6" cy="10.6" r="1.7" fill={hole} />
    </svg>
  )
}

/** Mark and wordmark, linking home. On the red band by default. */
export function Logo({ onBand = true }: { onBand?: boolean }) {
  return (
    <Link to="/" className="flex shrink-0 items-center gap-2 rounded-lg" aria-label="tradepost">
      <BrandMark size={34} inverse={onBand} />
      <span className="hidden text-[1.35rem] leading-none tracking-tight min-[340px]:inline">
        <span className="font-extrabold">trade</span>
        <span className="font-medium opacity-90">post</span>
      </span>
    </Link>
  )
}
