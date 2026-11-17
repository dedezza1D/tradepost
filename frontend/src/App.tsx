import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createBrowserRouter, Link, RouterProvider } from 'react-router-dom'
import { RequireAuth } from './components/RequireAuth'
import { Layout } from './components/Layout'
import { LoginPage, RegisterPage } from './pages/AuthPages'
import { ListingPage } from './pages/ListingPage'
import { MyListingsPage } from './pages/MyListingsPage'
import { SearchPage } from './pages/SearchPage'
import { SellPage } from './pages/SellPage'

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false } },
})

function NotFound() {
  return (
    <div className="card p-10 text-center">
      <p className="text-stone-700">Page not found.</p>
      <Link to="/" className="btn-secondary mt-4">
        Back to listings
      </Link>
    </div>
  )
}

const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: '/', element: <SearchPage /> },
      { path: '/listings/:id', element: <ListingPage /> },
      { path: '/login', element: <LoginPage /> },
      { path: '/register', element: <RegisterPage /> },
      { path: '/sell', element: <RequireAuth><SellPage key="new" /></RequireAuth> },
      { path: '/sell/:id', element: <RequireAuth><SellPage key="edit" /></RequireAuth> },
      { path: '/me/listings', element: <RequireAuth><MyListingsPage /></RequireAuth> },
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
