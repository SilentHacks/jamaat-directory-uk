def first_json_object(text: str, *, after: str | None = None) -> str | None:
    """Return the first balanced top-level ``{...}`` substring, or None.

    Brace-counts while skipping anything inside double-quoted strings, so prose,
    code fences, and string values containing braces do not confuse it. A naive
    ``\\{.*?\\}`` regex stops at the first ``}`` and breaks on nested JSON.

    ``after`` restricts the search to the first ``{`` that follows that marker
    substring (e.g. ``"confData"`` for an embedded JS config blob).
    """
    origin = 0
    if after is not None:
        origin = text.find(after)
        if origin == -1:
            return None
    start = text.find("{", origin)
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
