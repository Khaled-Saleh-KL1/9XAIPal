import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './index.css';
import { App } from './App';
import { AuthProvider } from './contexts/AuthContext';
import { ConfirmProvider } from './components/ConfirmDialog';
import { ImageLightbox } from './components/ImageLightbox';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AuthProvider>
      <ConfirmProvider>
        <App />
        {/* Mounted once at the root: it listens for clicks on any content
            image anywhere in the app rather than being wired per view. */}
        <ImageLightbox />
      </ConfirmProvider>
    </AuthProvider>
  </StrictMode>,
);
