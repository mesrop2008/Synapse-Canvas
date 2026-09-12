import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Navigate, RouterProvider, createBrowserRouter } from 'react-router-dom';

import { ApiError } from './api/client';
import { Layout } from './components/Layout';
import { ProtectedRoute } from './components/ProtectedRoute';
import { AuthProvider } from './hooks/useAuth';
import { LoginPage } from './pages/LoginPage';
import { NotFoundPage } from './pages/NotFoundPage';
import { RegisterPage } from './pages/RegisterPage';
import { VerifyEmailPage } from './pages/VerifyEmailPage';
import { WorkspacesPage } from './pages/WorkspacesPage';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Retrying a 403 or 404 just repeats the same answer. 401 is handled
      // inside the client by refresh-and-replay, so it never reaches here as
      // something worth retrying either.
      retry: (failureCount, error) =>
        error instanceof ApiError && error.status < 500 ? false : failureCount < 2,
      // Off because of the editor: a refetch triggered by tabbing back would
      // replace content the user is part-way through typing. Part 3's socket
      // makes freshness a push concern anyway.
      refetchOnWindowFocus: false,
      staleTime: 15_000,
    },
  },
});

const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  { path: '/register', element: <RegisterPage /> },
  { path: '/verify-email', element: <VerifyEmailPage /> },
  {
    element: (
      <ProtectedRoute>
        <Layout />
      </ProtectedRoute>
    ),
    children: [
      { index: true, element: <Navigate to="/workspaces" replace /> },
      { path: 'workspaces', element: <WorkspacesPage /> },
    ],
  },
  { path: '*', element: <NotFoundPage /> },
]);

export function App() {
  return (
    // AuthProvider sits inside QueryClientProvider because it clears the cache
    // on sign-out, and outside the router because the route guard reads it.
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>
  );
}
