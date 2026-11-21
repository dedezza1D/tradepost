import { useEffect, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { Icon } from '../ui/icons'

interface Slide {
  id: string
  to: string
  art: ReactNode
}

/** Illustration: a listing card being filled in, with a price tag. */
function SellArt() {
  return (
    <div className="relative h-full w-full">
      <div className="absolute top-1/2 left-1/2 w-44 -translate-x-1/2 -translate-y-1/2 -rotate-6 rounded-2xl bg-white p-3 shadow-[var(--shadow-lift)] sm:w-52">
        <div className="grid aspect-[4/3] place-items-center rounded-xl bg-[#fde8e8] text-[#c8102e]">
          <Icon name="camera" size={40} />
        </div>
        <div className="mt-3 h-3 w-3/4 rounded-full bg-[#ecdfd6]" />
        <div className="mt-2 h-3 w-1/2 rounded-full bg-[#ecdfd6]" />
        <div className="mt-3 h-5 w-20 rounded-full bg-[#c8102e]" />
      </div>
      <div className="absolute top-[18%] right-[12%] rotate-12 rounded-xl bg-[#ffd23f] px-3 py-2 text-sm font-extrabold text-[#24130f] shadow-[var(--shadow-card)]">
        R$ 120
      </div>
      <div className="absolute bottom-[16%] left-[10%] grid h-12 w-12 place-items-center rounded-full bg-white text-[#c8102e] shadow-[var(--shadow-card)]">
        <Icon name="sparkles" size={24} />
      </div>
    </div>
  )
}

/** Illustration: a shield over a parcel. */
function ProtectArt() {
  return (
    <div className="relative h-full w-full">
      <div className="absolute top-1/2 left-1/2 grid h-40 w-40 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-[2.5rem] bg-white/15 sm:h-48 sm:w-48">
        <div className="grid h-28 w-28 place-items-center rounded-[2rem] bg-white text-[#c8102e] shadow-[var(--shadow-lift)] sm:h-32 sm:w-32">
          <Icon name="shield" size={64} />
        </div>
      </div>
      <div className="absolute right-[14%] bottom-[14%] grid h-14 w-14 place-items-center rounded-2xl bg-[#ffd23f] text-[#24130f] shadow-[var(--shadow-card)]">
        <Icon name="box" size={28} />
      </div>
      <div className="absolute top-[16%] left-[14%] grid h-11 w-11 place-items-center rounded-full bg-white text-[#0b7a3b] shadow-[var(--shadow-card)]">
        <Icon name="check" size={22} />
      </div>
    </div>
  )
}

/** Illustration: a payment card with the installment count. */
function PayArt() {
  return (
    <div className="relative h-full w-full">
      <div className="absolute top-1/2 left-1/2 h-28 w-44 -translate-x-1/2 -translate-y-1/2 rotate-[-8deg] rounded-2xl bg-gradient-to-br from-[#24130f] to-[#5a1018] p-4 text-white shadow-[var(--shadow-lift)] sm:h-32 sm:w-52">
        <div className="h-6 w-8 rounded-md bg-[#ffd23f]" />
        <div className="mt-6 font-mono text-xs tracking-[0.2em] opacity-80">•••• 4242</div>
      </div>
      <div className="absolute top-[14%] right-[10%] rounded-2xl bg-white px-4 py-2 text-center shadow-[var(--shadow-card)]">
        <span className="block text-2xl leading-none font-extrabold text-[#c8102e]">12x</span>
      </div>
      <div className="absolute bottom-[14%] left-[12%] rounded-full bg-[#ffd23f] px-3 py-1.5 text-xs font-extrabold text-[#24130f] shadow-[var(--shadow-card)]">
        Pix
      </div>
    </div>
  )
}

const SLIDES: Slide[] = [
  { id: 'sell', to: '/sell', art: <SellArt /> },
  { id: 'protect', to: '/search', art: <ProtectArt /> },
  { id: 'pay', to: '/search', art: <PayArt /> },
]
const INTERVAL_MS = 7000

/**
 * Rotating banner about how the marketplace works. Rotation pauses on hover and keyboard focus,
 * and does not start at all for people who asked the system for reduced motion.
 */
export function HeroCarousel() {
  const { t } = useTranslation()
  const [index, setIndex] = useState(0)
  const [paused, setPaused] = useState(false)
  const reduced = typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

  useEffect(() => {
    if (paused || reduced) return
    const id = setInterval(() => setIndex((i) => (i + 1) % SLIDES.length), INTERVAL_MS)
    return () => clearInterval(id)
  }, [paused, reduced])

  const go = (step: number) => setIndex((i) => (i + step + SLIDES.length) % SLIDES.length)

  return (
    <section
      aria-roledescription="carousel"
      aria-label={t('home.hero.label')}
      className="group relative overflow-hidden rounded-3xl bg-gradient-to-br from-[#e1252f] via-[#c8102e] to-[#7a1420] text-white shadow-[var(--shadow-lift)]"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
    >
      {/* Soft shapes for depth. */}
      <div aria-hidden className="pointer-events-none absolute -top-24 -right-16 h-72 w-72 rounded-full bg-white/10" />
      <div aria-hidden className="pointer-events-none absolute -bottom-32 left-1/3 h-72 w-72 rounded-full bg-[#ffd23f]/15" />
      <div className="relative">
        {SLIDES.map((slide, i) => (
          <div
            key={slide.id}
            role="group"
            aria-roledescription="slide"
            aria-label={t('home.hero.position', { n: i + 1, total: SLIDES.length })}
            hidden={i !== index}
            className="grid min-h-[15rem] grid-cols-1 items-center gap-2 px-6 pt-7 pb-12 sm:min-h-[17rem] sm:grid-cols-[1.15fr_1fr] sm:px-10 sm:py-7"
          >
            <div className="max-w-md space-y-3">
              <span className="inline-flex rounded-full bg-white/15 px-3 py-1 text-xs font-bold tracking-wide uppercase">
                {t(`home.hero.${slide.id}.kicker`)}
              </span>
              <h2 className="text-2xl leading-tight font-extrabold tracking-tight text-balance sm:text-4xl">
                {t(`home.hero.${slide.id}.title`)}
              </h2>
              <p className="text-sm font-medium text-white sm:text-base">{t(`home.hero.${slide.id}.text`)}</p>
              <Link
                to={slide.to}
                className="mt-1 inline-flex min-h-11 items-center gap-2 rounded-full bg-white px-5 text-sm font-bold text-[#c8102e] shadow-sm hover:bg-white/90"
              >
                {t(`home.hero.${slide.id}.cta`)}
                <Icon name="chevronRight" size={18} />
              </Link>
            </div>
            <div className="hidden h-56 sm:block">{slide.art}</div>
          </div>
        ))}
      </div>
      <div className="absolute right-0 bottom-4 left-0 flex items-center justify-center gap-2">
        {SLIDES.map((slide, i) => (
          <button
            key={slide.id}
            type="button"
            onClick={() => setIndex(i)}
            aria-label={t('home.hero.goTo', { n: i + 1 })}
            aria-current={i === index}
            className="grid h-6 w-6 place-items-center"
          >
            <span className={`block h-2 rounded-full transition-all ${i === index ? 'w-6 bg-white' : 'w-2 bg-white/50'}`} />
          </button>
        ))}
      </div>
      <button
        type="button"
        onClick={() => go(-1)}
        aria-label={t('home.hero.previous')}
        className="absolute top-1/2 left-2 hidden h-10 w-10 -translate-y-1/2 place-items-center rounded-full bg-white/90 text-[#c8102e] opacity-0 shadow-[var(--shadow-card)] transition group-hover:opacity-100 focus-visible:opacity-100 md:grid"
      >
        <Icon name="chevronLeft" />
      </button>
      <button
        type="button"
        onClick={() => go(1)}
        aria-label={t('home.hero.next')}
        className="absolute top-1/2 right-2 hidden h-10 w-10 -translate-y-1/2 place-items-center rounded-full bg-white/90 text-[#c8102e] opacity-0 shadow-[var(--shadow-card)] transition group-hover:opacity-100 focus-visible:opacity-100 md:grid"
      >
        <Icon name="chevronRight" />
      </button>
    </section>
  )
}
