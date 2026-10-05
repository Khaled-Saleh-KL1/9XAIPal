export function DocumentTitle({ title }: { title: string }) {
  return /[\u0600-\u06ff]/u.test(title) ? <bdi dir="rtl">{title}</bdi> : title;
}
