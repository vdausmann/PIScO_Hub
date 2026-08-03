"""Human-readable table / JSON output helpers."""

import json


def print_json(data):
    print(json.dumps(data, indent=2))


def print_table(rows, columns):
    """rows: list of dicts; columns: list of (header, key)."""
    if not rows:
        print("(none)")
        return
    widths = {}
    for header, key in columns:
        widths[key] = max(len(header),
                          *(len(_cell(row.get(key))) for row in rows))
    header_line = "  ".join(h.ljust(widths[k]) for h, k in columns)
    print(header_line)
    print("-" * len(header_line))
    for row in rows:
        print("  ".join(_cell(row.get(k)).ljust(widths[k]) for _, k in columns))


def _cell(value):
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)
