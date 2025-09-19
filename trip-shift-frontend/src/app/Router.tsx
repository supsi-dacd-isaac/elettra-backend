import type { ReactElement } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import AppLayout from './layouts/AppLayout.tsx';
import LoginPage from './pages/LoginPage.tsx';
import PlannerPage from './pages/PlannerPage.tsx';
import ShiftsPage from './pages/ShiftsPage.tsx';
import DepotsPage from './pages/DepotsPage.tsx';
import BusModelsPage from './pages/BusModelsPage.tsx';
import BusesPage from './pages/BusesPage.tsx';
import UserPage from './pages/UserPage.tsx';
import { useAuth } from './auth/AuthContext.tsx';

function Protected({ children }: { children: ReactElement }) {
  const { token } = useAuth();
  if (!token) return <Navigate to="/login" replace />;
  return children;
}

export default function AppRouter() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppLayout />}> 
          <Route index element={<Navigate to="/planner" replace />} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/planner" element={<Protected><PlannerPage /></Protected>} />
          <Route path="/user" element={<Protected><UserPage /></Protected>} />
          <Route path="/shifts" element={<Protected><ShiftsPage /></Protected>} />
          <Route path="/depots" element={<Protected><DepotsPage /></Protected>} />
          <Route path="/fleet/models" element={<Protected><BusModelsPage /></Protected>} />
          <Route path="/fleet/buses" element={<Protected><BusesPage /></Protected>} />
          <Route path="*" element={<Navigate to="/planner" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}


