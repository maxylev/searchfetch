"""Parsing for ordered CSS selector fallbacks shared by extraction entry points."""


def split_selectors(value: str = "") -> list[str]:
    """Preserve commas inside CSS functions, quoted attributes, and escapes."""
    selectors = []
    start = depth = 0
    quote = None
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in ('"', "'"):
            quote = char
        elif char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif char == "," and depth == 0:
            selectors.append(value[start:index].strip())
            start = index + 1
    selectors.append(value[start:].strip())
    return selectors
