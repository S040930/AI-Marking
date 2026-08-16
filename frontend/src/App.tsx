import { RouterProvider } from 'react-router-dom';
import { QueryClientProvider } from '@tanstack/react-query';
import { Toaster } from '@/components/ui/sonner';
import { router } from '@/router';
import { queryClient } from '@/api/client';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import AuthGate from '@/components/AuthGate';

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ErrorBoundary>
        <AuthGate>
          <RouterProvider router={router} />
        </AuthGate>
      </ErrorBoundary>
      <Toaster richColors position="top-center" />
    </QueryClientProvider>
  );
}
