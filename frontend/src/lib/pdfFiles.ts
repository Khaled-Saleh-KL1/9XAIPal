export function isPdfFile(file: File): boolean {
  return file.type === 'application/pdf' || /\.pdf$/i.test(file.name);
}

export function pickFirstPdf(files: File[]): {
  pdf: File | null;
  rejected: File[];
  extraPdfs: number;
} {
  const pdfs = files.filter(isPdfFile);
  return {
    pdf: pdfs[0] ?? null,
    rejected: files.filter((file) => !isPdfFile(file)),
    extraPdfs: Math.max(0, pdfs.length - 1),
  };
}
