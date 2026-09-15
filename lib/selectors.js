/** Split fallback selectors without breaking CSS functions, attributes, or escapes. */
export function splitSelectors(value = "") {
  const selectors = [];
  let start = 0;
  let depth = 0;
  let quote = null;
  for (let i = 0; i < value.length; i++) {
    const char = value[i];
    if (char === "\\") {
      i++;
      continue;
    }
    if (quote) {
      if (char === quote) quote = null;
      continue;
    }
    if (char === '"' || char === "'") quote = char;
    else if (char === "(" || char === "[") depth++;
    else if (char === ")" || char === "]") depth--;
    else if (char === "," && depth === 0) {
      selectors.push(value.slice(start, i).trim());
      start = i + 1;
    }
  }
  selectors.push(value.slice(start).trim());
  return selectors;
}
