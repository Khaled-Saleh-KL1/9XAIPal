import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './index.css';
import { App } from './App';
import { AppProviders } from './AppProviders';
import { AuthProvider } from './contexts/AuthContext';
import { initInstallCapture } from './pwa/installPrompt';
import { registerServiceWorker } from './pwa/register';

initInstallCapture();
registerServiceWorker();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AuthProvider>
      <AppProviders>
        <App />
      </AppProviders>
    </AuthProvider>
  </StrictMode>,
);
