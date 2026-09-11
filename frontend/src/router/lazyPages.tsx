import { lazy } from 'react';

// Keep route-level code splitting while leaving the router module data-only.
export const HistoryPage = lazy(() => import('@/pages/HistoryPage'));
export const QuestionsPage = lazy(() => import('@/pages/QuestionsPage'));
export const UploadQuestionsPage = lazy(() => import('@/pages/UploadQuestionsPage'));
export const UploadSubmissionPage = lazy(() => import('@/pages/UploadSubmissionPage'));
export const ResultPage = lazy(() => import('@/pages/ResultPage'));
export const ReviewPage = lazy(() => import('@/pages/ReviewPage'));
export const SettingsPage = lazy(() => import('@/pages/SettingsPage'));

