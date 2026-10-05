import type { ReactNode } from 'react';
import { ConfirmProvider } from './components/ConfirmDialog';
import { ImageLightbox } from './components/ImageLightbox';
import { MotionRoot } from './motion';

/**
 * The app's UI providers, in the one order that works.
 *
 * ⚠ MotionRoot must wrap ConfirmProvider. The confirm sheet renders inside
 * ConfirmProvider and is built from motion components; outside MotionRoot's
 * LazyMotion they never load their animation features and stay at their
 * initial hidden state, which made every delete confirmation invisible while
 * its Delete button already had focus.
 */
export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <MotionRoot>
      <ConfirmProvider>
        {children}
        {/* Mounted once at the root: it listens for clicks on any content
            image anywhere in the app rather than being wired per view. */}
        <ImageLightbox />
      </ConfirmProvider>
    </MotionRoot>
  );
}
