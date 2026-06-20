# src/directory/ingest/author.py
def extract_json(text: str) -> str | None:
    """Return the first balanced top-level JSON object in ``text``, or None.

    Brace-counts while skipping anything inside double-quoted strings, so prose,
    code fences, and string values containing braces do not confuse it.
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None
