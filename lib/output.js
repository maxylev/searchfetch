/** Paginate Unicode code points so offsets agree with Python and never split a surrogate pair. */
export function paginate(text, templateName, startIndex, maxLength) {
  const characters = Array.from(text);
  const start = Math.min(startIndex, characters.length);
  const page = characters.slice(start, start + maxLength);
  const end = start + page.length;
  const next = end < characters.length ? ` Use start_index=${end} to read more.` : "";
  return `${page.join("")}\n\n---\n[webfetch: template="${templateName}", showing characters ${start} to ${end} of ${characters.length} total.${next}]`;
}
