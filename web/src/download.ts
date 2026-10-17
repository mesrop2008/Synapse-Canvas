/** Hands a blob to the browser as a download. */
export function saveFile(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.rel = 'noopener';
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Some browsers start reading the URL only after the click returns.
  window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
}
