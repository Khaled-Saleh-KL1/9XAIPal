import type { SVGProps } from 'react';

type IconProps = SVGProps<SVGSVGElement>;

const base: IconProps = {
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.5,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
};

export function IconSearch(p: IconProps) {
  return <svg {...base} {...p}><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>;
}
export function IconPlus(p: IconProps) {
  return <svg {...base} {...p}><path d="M12 5v14M5 12h14" /></svg>;
}
export function IconUpload(p: IconProps) {
  return <svg {...base} {...p}><path d="M12 16V4m0 0-4 4m4-4 4 4" /><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" /></svg>;
}
export function IconDoc(p: IconProps) {
  return <svg {...base} {...p}><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" /></svg>;
}
export function IconPin(p: IconProps) {
  return <svg {...base} {...p}><path d="M12 17v5" /><path d="M9 3h6l-1 6 3 3v2H7v-2l3-3z" /></svg>;
}
export function IconSort(p: IconProps) {
  return <svg {...base} {...p}><path d="M7 4v16m0 0-3-3m3 3 3-3" /><path d="M17 20V4m0 0-3 3m3-3 3 3" /></svg>;
}
export function IconCheck(p: IconProps) {
  return <svg {...base} {...p}><path d="m5 12 5 5L20 7" /></svg>;
}
export function IconSend(p: IconProps) {
  return <svg {...base} {...p}><path d="M5 12h14M13 6l6 6-6 6" /></svg>;
}
export function IconArrow(p: IconProps) {
  return <svg {...base} {...p}><path d="M5 12h14m-6-6 6 6-6 6" /></svg>;
}
export function IconBack(p: IconProps) {
  return <svg {...base} {...p}><path d="M19 12H5m6 6-6-6 6-6" /></svg>;
}
export function IconGrid(p: IconProps) {
  return (
    <svg {...base} {...p}>
      <rect x="4" y="4" width="7" height="7" rx="1" />
      <rect x="13" y="4" width="7" height="7" rx="1" />
      <rect x="4" y="13" width="7" height="7" rx="1" />
      <rect x="13" y="13" width="7" height="7" rx="1" />
    </svg>
  );
}
export function IconList(p: IconProps) {
  return <svg {...base} {...p}><path d="M4 6h16M4 12h16M4 18h16" /></svg>;
}
export function IconSpinner(p: IconProps) {
  return (
    <svg {...base} {...p}>
      <circle cx="12" cy="12" r="9" opacity="0.18" />
      <path d="M21 12a9 9 0 0 0-9-9" strokeLinecap="round" />
    </svg>
  );
}
