import { useCallback, useEffect, useState } from 'react';

export const WELCOME_HASH = '#/welcome';

export function isWelcomeHash(hash = typeof window === 'undefined' ? '' : window.location.hash) {
  return hash === WELCOME_HASH;
}

export function useWelcomeRoute(): [boolean, (open: boolean) => void] {
  const [welcome, setWelcomeState] = useState(() => isWelcomeHash());

  useEffect(() => {
    const sync = () => setWelcomeState(isWelcomeHash());
    window.addEventListener('hashchange', sync);
    window.addEventListener('popstate', sync);
    return () => {
      window.removeEventListener('hashchange', sync);
      window.removeEventListener('popstate', sync);
    };
  }, []);

  const setWelcome = useCallback((open: boolean) => {
    const next = open ? WELCOME_HASH : '#/library';
    if (window.location.hash !== next) window.history.pushState(null, '', next);
    setWelcomeState(open);
  }, []);

  return [welcome, setWelcome];
}
