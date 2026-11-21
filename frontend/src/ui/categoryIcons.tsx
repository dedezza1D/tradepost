/** Line icons for the catalog categories (24px grid, 2px stroke, currentColor). */
const paths: Record<string, string> = {
  electronics: 'M3 4h18v12H3zM8 20h8M12 16v4',
  phones: 'M8 2h8a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2ZM11 18h2',
  computers: 'M4 5h16v10H4zM2 19h20',
  games:
    'M6 11h4M8 9v4M15 12h.01M18 10h.01M7.5 6h9a5.5 5.5 0 0 1 5.3 7l-.8 3a2.5 2.5 0 0 1-4.2 1.2L15 15H9l-1.8 2.2A2.5 2.5 0 0 1 3 16l-.8-3A5.5 5.5 0 0 1 7.5 6Z',
  home: 'M4 10h16v6a4 4 0 0 1-4 4H8a4 4 0 0 1-4-4v-6ZM2 10h20M9 3v3M15 3v3',
  furniture: 'M5 11V8a3 3 0 0 1 3-3h8a3 3 0 0 1 3 3v3M2 13a2 2 0 0 1 4 0v2h12v-2a2 2 0 0 1 4 0v5H2v-5ZM5 18v2M19 18v2',
  appliances: 'M6 2h12a1 1 0 0 1 1 1v18a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1ZM5 10h14M9 5v2M9 13v3',
  fashion: 'M8 3 3 6l2 4 2-1v12h10V9l2 1 2-4-5-3a4 4 0 0 1-8 0Z',
  shoes: 'M3 17V8l4 1 3 3h3l6 2a3 3 0 0 1 2 3v1H3v-1ZM3 18h18M10 12l1-1.5M13 13l1-1.5',
  beauty: 'M9 2h6v4H9zM7.5 6h9l1 3v12h-11V9zM10 13h4',
  sports: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18ZM3.6 9h16.8M3.6 15h16.8M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18',
  bikes:
    'M2 15.5a3.5 3.5 0 1 0 7 0 3.5 3.5 0 1 0-7 0M15 15.5a3.5 3.5 0 1 0 7 0 3.5 3.5 0 1 0-7 0M5.5 15.5 9 8h6l3.5 7.5M9 8l3 7.5M14 5h2.5l-1.5 3',
  toys: 'M4 13h7v7H4zM13 13h7v7h-7zM8.5 4h7v7h-7z',
  baby: 'M4 10h13a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5ZM12 10V3a7 7 0 0 1 7 7M7 19.5a1.5 1.5 0 1 0 3 0 1.5 1.5 0 1 0-3 0M14 19.5a1.5 1.5 0 1 0 3 0 1.5 1.5 0 1 0-3 0M2 6h2l1.5 4',
  books: 'M3 4h6a3 3 0 0 1 3 3v14a2 2 0 0 0-2-2H3zM21 4h-6a3 3 0 0 0-3 3v14a2 2 0 0 1 2-2h7z',
  music: 'M9 18V5l11-2v13M3 18a3 3 0 1 0 6 0 3 3 0 1 0-6 0M14 16a3 3 0 1 0 6 0 3 3 0 1 0-6 0',
  tools:
    'M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.8-3.8a6 6 0 0 1-7.9 7.9l-6.9 6.9a2.1 2.1 0 0 1-3-3l6.9-6.9a6 6 0 0 1 7.9-7.9z',
  'auto-parts': 'M5 17h14M3 13l2-6h14l2 6v4H3zM7 17v2M17 17v2M7 13h.01M17 13h.01',
  pets:
    'M12 13c-3 0-5 3.2-5 5.2A1.8 1.8 0 0 0 8.8 20c1.2 0 2-.8 3.2-.8s2 .8 3.2.8a1.8 1.8 0 0 0 1.8-1.8c0-2-2-5.2-5-5.2ZM4 10.5a1.5 2 0 1 0 3 0 1.5 2 0 1 0-3 0M17 10.5a1.5 2 0 1 0 3 0 1.5 2 0 1 0-3 0M7.5 6a1.5 2 0 1 0 3 0 1.5 2 0 1 0-3 0M13.5 6a1.5 2 0 1 0 3 0 1.5 2 0 1 0-3 0',
  collectibles: 'M12 2.5 15 8.6l6.6 1-4.8 4.6 1.2 6.6L12 17.6l-6 3.2 1.2-6.6-4.8-4.6 6.6-1z',
  other: 'M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z',
}

export function CategoryIcon({ slug, size = 24, className = '' }: { slug: string; size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className={className}
    >
      <path d={paths[slug] ?? paths.other} />
    </svg>
  )
}
