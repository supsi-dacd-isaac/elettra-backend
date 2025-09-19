import { AuthProvider } from './app/auth/AuthContext';
import AppRouter from './app/Router';

export default function App() {
  return (
    <AuthProvider>
      <AppRouter />
    </AuthProvider>
  );
}
