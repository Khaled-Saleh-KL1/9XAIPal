import { useEffect, useState } from 'react';
import type { RefObject } from 'react';
import { useInView } from 'motion/react';

export function usePauseWhenHidden(ref: RefObject<Element | null>): boolean {
  const inView = useInView(ref, { margin: '100px' });
  const [documentVisible, setDocumentVisible] = useState(() => typeof document === 'undefined' || !document.hidden);

  useEffect(() => {
    const updateVisibility = () => setDocumentVisible(!document.hidden);
    document.addEventListener('visibilitychange', updateVisibility);
    return () => document.removeEventListener('visibilitychange', updateVisibility);
  }, []);

  return inView && documentVisible;
}
