import { m, useReducedMotion } from 'motion/react';
import { calm, playful } from './springs';

export function RollingNumber({ value }: { value: number }) {
  const reducedMotion = useReducedMotion();
  const text = String(Math.trunc(value));

  return (
    <span className="inline-flex items-baseline font-mono tabular-nums" aria-label={text}>
      <span className="sr-only" role="status" aria-live="polite" aria-atomic="true">{text}</span>
      <span aria-hidden="true" className="inline-flex">
        {reducedMotion ? text : Array.from(text).map((character, index) => {
          if (!/\d/.test(character)) return <span key={`${index}-${character}`}>{character}</span>;
          const digit = Number(character);
          return (
            <span key={index} className="inline-block h-[1em] w-[0.62em] overflow-hidden align-bottom leading-none">
              <m.span
                className="block"
                initial={false}
                animate={{ y: `-${digit * 10}%` }}
                transition={reducedMotion ? calm : playful}
              >
                {'0123456789'.split('').map((number) => (
                  <span key={number} className="block h-[1em] leading-none">{number}</span>
                ))}
              </m.span>
            </span>
          );
        })}
      </span>
    </span>
  );
}
