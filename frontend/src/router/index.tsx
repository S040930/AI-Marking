import { createBrowserRouter, Navigate } from 'react-router-dom';
import MainLayout from '@/layouts/MainLayout';
import HomePage from '@/pages/HomePage';
import {
  HistoryPage,
  QuestionsPage,
  ResultPage,
  ReviewPage,
  SettingsPage,
  UploadQuestionsPage,
} from './lazyPages';

export const router = createBrowserRouter([
  {
    path: '/',
    element: <MainLayout />,
    children: [
      { index: true, element: <HomePage /> },
      { path: 'history', element: <HistoryPage /> },
      { path: 'questions', element: <QuestionsPage /> },
      { path: 'questions/upload', element: <UploadQuestionsPage /> },
      { path: 'result/:id', element: <ResultPage /> },
      { path: 'review/:id', element: <ReviewPage /> },
      { path: 'settings', element: <SettingsPage /> },
      { path: '*', element: <Navigate to="/" replace /> },
    ],
  },
]);
