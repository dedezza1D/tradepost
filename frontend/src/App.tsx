import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { RequireAuth } from './components/RequireAuth'
import { LoginPage, RegisterPage } from './pages/AuthPages'
import { HomePage } from './pages/HomePage'
import { ListingPage } from './pages/ListingPage'
import { SearchPage } from './pages/SearchPage'
import { EmptyState, Spinner } from './ui/primitives'

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, staleTime: 15_000 } },
})

function NotFound() {
  const { t } = useTranslation()
  return <EmptyState icon="search" title={t('common.notFound')} action={{ label: t('listing.backToListings'), to: '/' }} />
}

function RouteLoading() {
  return (
    <div className="flex justify-center py-16 text-primary">
      <Spinner />
    </div>
  )
}

const auth = (node: ReactNode) => <RequireAuth>{node}</RequireAuth>

// Browsing pages ship in the main bundle; account, selling and payment pages (with the Stripe
// and Mercado Pago SDKs) load only when opened.
const router = createBrowserRouter([
  {
    element: <AppShell />,
    hydrateFallbackElement: <RouteLoading />,
    children: [
      { path: '/', element: <HomePage /> },
      { path: '/search', element: <SearchPage /> },
      { path: '/listings/:id', element: <ListingPage /> },
      { path: '/login', element: <LoginPage /> },
      { path: '/register', element: <RegisterPage /> },
      {
        path: '/users/:id',
        lazy: async () => ({ Component: (await import('./pages/SellerProfilePage')).SellerProfilePage }),
      },
      {
        path: '/me',
        lazy: async () => {
          const { ProfilePage } = await import('./pages/ProfilePage')
          return { element: auth(<ProfilePage />) }
        },
      },
      {
        path: '/me/listings',
        lazy: async () => {
          const { MyListingsPage } = await import('./pages/MyListingsPage')
          return { element: auth(<MyListingsPage />) }
        },
      },
      {
        path: '/notifications',
        lazy: async () => {
          const { NotificationsPage } = await import('./pages/NotificationsPage')
          return { element: auth(<NotificationsPage />) }
        },
      },
      {
        path: '/sell',
        lazy: async () => {
          const { SellPage } = await import('./pages/SellPage')
          return { element: auth(<SellPage key="new" />) }
        },
      },
      {
        path: '/sell/:id',
        lazy: async () => {
          const { SellPage } = await import('./pages/SellPage')
          return { element: auth(<SellPage key="edit" />) }
        },
      },
      {
        path: '/cart',
        lazy: async () => {
          const { CartPage } = await import('./pages/CartPage')
          return { element: auth(<CartPage />) }
        },
      },
      {
        path: '/checkout',
        lazy: async () => {
          const { CheckoutStartPage } = await import('./pages/CheckoutStartPage')
          return { element: auth(<CheckoutStartPage />) }
        },
      },
      {
        path: '/checkout/:id',
        lazy: async () => {
          const { CheckoutPage } = await import('./pages/CheckoutPage')
          return { element: auth(<CheckoutPage />) }
        },
      },
      {
        path: '/orders',
        lazy: async () => {
          const { OrdersPage } = await import('./pages/OrdersPage')
          return { element: auth(<OrdersPage />) }
        },
      },
      {
        path: '/seller',
        lazy: async () => {
          const { SellerPage } = await import('./pages/SellerPage')
          return { element: auth(<SellerPage />) }
        },
      },
      { path: '*', element: <NotFound /> },
    ],
  },
])

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
}
