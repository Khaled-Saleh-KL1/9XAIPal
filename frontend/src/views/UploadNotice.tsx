import { useEffect, useState } from 'react';

export type BackpressureCode = 'queue_full' | 'user_queue_full' | 'storage_full' | 'service_unavailable';
const messages: Record<BackpressureCode, string> = {
  queue_full: 'The server is busy processing other uploads.',
  user_queue_full: 'Your earlier uploads are still processing.',
  storage_full: 'The server needs more storage space before accepting uploads.',
  service_unavailable: 'Uploads are temporarily unavailable.',
};

export function UploadNotice({code, retryAfter = 120, duplicate = false, onRetry, onOpen}: {
  code?: BackpressureCode; retryAfter?: number; duplicate?: boolean; onRetry?: () => void; onOpen?: () => void;
}) {
  const [remaining, setRemaining] = useState(retryAfter);
  useEffect(() => {
    if (!code) return;
    setRemaining(retryAfter);
    const timer = setInterval(() => setRemaining(value => Math.max(0, value - 1)), 1000);
    return () => clearInterval(timer);
  }, [code, retryAfter]);
  useEffect(() => {
    if (code && remaining === 0) onRetry?.();
  }, [code, remaining, onRetry]);
  if (duplicate) return <div role="status" className="mx-7 mb-5 px-4 py-3"><p>This file is already in your library</p>{onOpen && <button type="button" onClick={onOpen}>Open document</button>}</div>;
  if (!code) return null;
  return <div role="status" className="mx-7 mb-5 px-4 py-3 rounded-md" style={{background:'var(--bg-2)',color:'var(--fg)',border:'1px solid var(--border)'}}>
    <p>{messages[code]}</p>
    <p>{remaining > 0 ? `Try again in ${remaining} seconds.` : 'You can try again now.'}</p>
    <p>Your library is safe. Please try again after the wait.</p>
    <button type="button" disabled={remaining > 0} onClick={onRetry}>Try again</button>
  </div>;
}
