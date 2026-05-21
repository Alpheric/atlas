import React, { Suspense } from 'react';
import { Routes, Route } from 'react-router-dom';
import AppLayout from './components/layout/AppLayout';
import ErrorBoundary from './components/shared/ErrorBoundary';
import PageSkeleton from './components/shared/PageSkeleton';
import LoginPage from './components/auth/LoginPage';
import ProtectedRoute from './components/auth/ProtectedRoute';

// Lazy-load pages
const Overview = React.lazy(() => import('./pages/Overview'));
const Conversations = React.lazy(() => import('./pages/Conversations'));
const ConversationDetail = React.lazy(() => import('./pages/ConversationDetail'));
const Routing = React.lazy(() => import('./pages/Routing'));
const Providers = React.lazy(() => import('./pages/Providers'));
const Training = React.lazy(() => import('./pages/Training'));
const Analytics = React.lazy(() => import('./pages/Analytics'));
const Import = React.lazy(() => import('./pages/Import'));
const SettingsPage = React.lazy(() => import('./pages/Settings'));
const Accounts = React.lazy(() => import('./pages/Accounts'));
const Models = React.lazy(() => import('./pages/Models'));
const Playground = React.lazy(() => import('./pages/Playground'));
const Users = React.lazy(() => import('./pages/Users'));
const Healing = React.lazy(() => import('./pages/Healing'));
const Prompts = React.lazy(() => import('./pages/Prompts'));
const Evals = React.lazy(() => import('./pages/Evals'));
const CostOps = React.lazy(() => import('./pages/CostOps'));

function PageWrapper({ children, skeleton }: { children: React.ReactNode; skeleton?: string }) {
  return (
    <ErrorBoundary>
      <Suspense fallback={<PageSkeleton type={skeleton as any} />}>
        {children}
      </Suspense>
    </ErrorBoundary>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/*"
        element={
          <ProtectedRoute>
            <AppLayout>
              <Routes>
                <Route path="/" element={<PageWrapper><Overview /></PageWrapper>} />
                <Route path="/conversations" element={<PageWrapper skeleton="table"><Conversations /></PageWrapper>} />
                <Route path="/conversations/:id" element={<PageWrapper skeleton="detail"><ConversationDetail /></PageWrapper>} />
                <Route path="/routing" element={<PageWrapper skeleton="table"><Routing /></PageWrapper>} />
                <Route path="/providers" element={<PageWrapper><Providers /></PageWrapper>} />
                <Route path="/accounts" element={<PageWrapper skeleton="table"><Accounts /></PageWrapper>} />
                <Route path="/models" element={<PageWrapper><Models /></PageWrapper>} />
                <Route path="/training" element={<PageWrapper><Training /></PageWrapper>} />
                <Route path="/analytics" element={<PageWrapper><Analytics /></PageWrapper>} />
                <Route path="/import" element={<PageWrapper skeleton="form"><Import /></PageWrapper>} />
                <Route path="/playground" element={<PageWrapper><Playground /></PageWrapper>} />
                <Route path="/settings" element={<PageWrapper skeleton="form"><SettingsPage /></PageWrapper>} />
                <Route path="/users" element={<PageWrapper skeleton="table"><Users /></PageWrapper>} />
                <Route path="/healing" element={<PageWrapper><Healing /></PageWrapper>} />
                <Route path="/prompts" element={<PageWrapper skeleton="table"><Prompts /></PageWrapper>} />
                <Route path="/evals" element={<PageWrapper skeleton="table"><Evals /></PageWrapper>} />
                <Route path="/cost" element={<PageWrapper><CostOps /></PageWrapper>} />
              </Routes>
            </AppLayout>
          </ProtectedRoute>
        }
      />
    </Routes>
  );
}
