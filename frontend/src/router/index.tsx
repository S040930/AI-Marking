import { lazy } from 'react';
import { createBrowserRouter, Navigate } from 'react-router-dom';
import MainLayout from '@/layouts/MainLayout';
import UploadPage from '@/pages/UploadPage';

// 非首屏页面懒加载,减小首屏 bundle 体积
const HistoryPage = lazy(() => import('@/pages/HistoryPage'));
const QuestionsPage = lazy(() => import('@/pages/QuestionsPage'));
const ResultPage = lazy(() => import('@/pages/ResultPage'));
const ReviewPage = lazy(() => import('@/pages/ReviewPage'));
const SettingsPage = lazy(() => import('@/pages/SettingsPage'));

export const router = createBrowserRouter([
  {
    path: '/',
    element: <MainLayout />,
    children: [
      { index: true, element: <UploadPage /> },
      { path: 'history', element: <HistoryPage /> },
      { path: 'questions', element: <QuestionsPage /> },
      { path: 'result/:id', element: <ResultPage /> },
      { path: 'review/:id', element: <ReviewPage /> },
      { path: 'settings', element: <SettingsPage /> },
      { path: '*', element: <Navigate to="/" replace /> },
    ],
  },
]);
