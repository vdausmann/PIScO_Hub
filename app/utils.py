import unicodedata

def clean_string(s: str) -> str:
    # Normalize Unicode (removes weird composed characters)
    s = unicodedata.normalize("NFKC", s)

    # Remove BOM if present
    s = s.replace("\ufeff", "")

    # Remove carriage returns and null bytes explicitly
    s = s.replace("\r", "").replace("\x00", "")

    # Remove all other non-printable/control characters
    s = "".join(
        ch for ch in s
        if ch.isprintable() or ch in "\n\t"
    )

    # Optional: strip leading/trailing whitespace (including newlines)
    return s.strip()

