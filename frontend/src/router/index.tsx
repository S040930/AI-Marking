import { createBrowserRouter, Navigate } from 'react-router-dom';
import MainLayout from '@/layouts/MainLayout';
import UploadPage from '@/pages/UploadPage';
import HistoryPage from '@/pages/HistoryPage';
import ResultPage from '@/pages/ResultPage';
import SettingsPage from '@/pages/SettingsPage';

export const router = createBrowserRouter([
  {
    path: '/',
    element: <MainLayout />,
    children: [
      { index: true, element: <UploadPage /> },
      { path: 'history', element: <HistoryPage /> },
      { path: 'result/:id', element: <ResultPage /> },
      { path: 'settings', element: <SettingsPage /> },
      { path: '*', element: <Navigate to="/" replace /> },
    ],
  },
]);
