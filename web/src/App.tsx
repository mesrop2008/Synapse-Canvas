import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Navigate, RouterProvider, createBrowserRouter } from 'react-router-dom';

import { ApiError } from './api/client';
import { Layout } from './components/Layout';
import { ProtectedRoute } from './components/ProtectedRoute';
import { AuthProvider } from './hooks/useAuth';
import { I18nProvider } from './hooks/useI18n';
import { ThemeProvider } from './hooks/useTheme';
import { AccountPage } from './pages/AccountPage';
import { DocumentPage } from './pages/DocumentPage';
import { LoginPage } from './pages/LoginPage';
import { NotFoundPage } from './pages/NotFoundPage';
import { RegisterPage } from './pages/RegisterPage';
import { VerifyEmailPage } from './pages/VerifyEmailPage';
import { WorkspacePage } from './pages/WorkspacePage';
import { WorkspacesPage } from './pages/WorkspacesPage';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Retrying a 4xx repeats the same answer; 401 is already handled by
      // refresh-and-replay inside the client.
      retry: (failureCount, error) =>
        error instanceof ApiError && error.status < 500 ? false : failureCount < 2,
      // A refetch on tab-back would replace content being typed.
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
      { path: 'account', element: <AccountPage /> },
      { path: 'workspaces', element: <WorkspacesPage /> },
      { path: 'workspaces/:workspaceId', element: <WorkspacePage /> },
      {
        path: 'workspaces/:workspaceId/documents/:documentId',
        element: <DocumentPage />,
      },
    ],
  },
  { path: '*', element: <NotFoundPage /> },
]);

export function App() {
  return (
    // I18nProvider outermost: the fetch client translates its errors, so the
    // language must be settled before anything can fail. AuthProvider needs the
    // query client (it clears the cache on sign-out) and the router needs it.
    <I18nProvider>
      <ThemeProvider>
        <QueryClientProvider client={queryClient}>
          <AuthProvider>
            <RouterProvider router={router} />
          </AuthProvider>
        </QueryClientProvider>
      </ThemeProvider>
    </I18nProvider>
  );
}
