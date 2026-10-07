import { useRef, useState } from 'react';
import { INSTALL } from '../landing/content';
import { Pressable, Sheet } from '../motion';
import { useInstallPrompt } from './installPrompt';
import { detectPlatform, isStandalone, readPlatformInput } from './platform';

function ShareIcon() {
  return (
    <svg className="install-step-icon" viewBox="0 0 24 24" role="img" aria-label={INSTALL.shareIconLabel} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 15V3" />
      <path d="M8 7l4-4 4 4" />
      <path d="M7 10H6a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-7a2 2 0 0 0-2-2h-1" />
    </svg>
  );
}

function AddIcon() {
  return (
    <svg className="install-step-icon" viewBox="0 0 24 24" role="img" aria-label={INSTALL.addIconLabel} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="4" y="4" width="16" height="16" rx="4" />
      <path d="M12 8v8M8 12h8" />
    </svg>
  );
}

type Mode = 'ios' | 'qr';

export function InstallApp() {
  const { canPrompt, installed, promptInstall } = useInstallPrompt();
  const [mode, setMode] = useState<Mode | null>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  // Keep the content mounted while the sheet animates out.
  const lastMode = useRef<Mode>('qr');
  if (mode) lastMode.current = mode;
  const shown = mode ?? lastMode.current;

  const env = readPlatformInput();
  if (installed || isStandalone(env)) return null;

  const onClick = () => {
    if (canPrompt) {
      void promptInstall().catch(() => {});
    } else if (detectPlatform(env) === 'ios') {
      setMode('ios');
    } else {
      setMode('qr');
    }
  };

  const close = () => setMode(null);

  return (
    <div className="landing-install">
      <Pressable ref={buttonRef} className="landing-secondary-cta landing-install-button" onClick={onClick}>
        <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 3v12" /><path d="M7 11l5 5 5-5" /><path d="M5 20h14" />
        </svg>
        {INSTALL.label}
      </Pressable>
      <p className="landing-install-subtitle">{INSTALL.subtitle}</p>

      <Sheet open={mode !== null} onClose={close} labelledBy="install-title" initialFocusRef={closeRef} returnFocusRef={buttonRef} panelClassName="install-panel">
        {shown === 'ios' && (
          <>
            <h2 id="install-title" className="install-title">{INSTALL.iosTitle}</h2>
            <p className="install-intro">{INSTALL.iosIntro}</p>
            <ol className="install-steps">
              <li><span className="install-step-art"><ShareIcon /></span><span><strong>1.</strong> {INSTALL.iosStep1}</span></li>
              <li><span className="install-step-art"><AddIcon /></span><span><strong>2.</strong> {INSTALL.iosStep2}</span></li>
            </ol>
          </>
        )}
        {shown === 'qr' && (
          <>
            <h2 id="install-title" className="install-title">{INSTALL.qrTitle}</h2>
            <p className="install-intro">{INSTALL.qrIntro}</p>
            <img className="install-qr" src="/qr-9xaipal.svg" alt={INSTALL.qrAlt} width="200" height="200" />
            <p className="install-url">{INSTALL.qrUrl}</p>
            <ul className="install-notes">
              <li>{INSTALL.qrAndroid}</li>
              <li>{INSTALL.qrIphone}</li>
            </ul>
          </>
        )}
        <button ref={closeRef} type="button" className="install-close" onClick={close}>{INSTALL.close}</button>
      </Sheet>
    </div>
  );
}
