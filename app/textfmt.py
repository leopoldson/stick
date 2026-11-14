"""Tidy what students type before it's stored: 'discrete structures' -> 'Discrete Structures'."""

SMALL = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or", "the", "to", "vs", "with"}
ROMAN = {"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"}
ACRONYMS = {"ai", "ict", "oop", "sql", "dsa", "hci", "gis", "cpu", "api", "ui", "ux", "html", "css", "uml", "os"}


def _cap(part: str) -> str:
    """Capitalise the first letter, skipping leading punctuation like '('."""
    for i, ch in enumerate(part):
        if ch.isalpha():
            return part[:i] + ch.upper() + part[i + 1:].lower()
    return part


def _word(word: str, first: bool) -> str:
    low = word.lower()
    if any(ch.isdigit() for ch in word):
        return word.upper()                      # course codes: csc301 -> CSC301
    if word.isupper() and len(word) <= 4:
        return word                              # acronyms typed in capitals: AI, SQL
    if word != low and not word.isupper():
        return word                              # mixed case typed on purpose: iPhone
    if low in ACRONYMS:
        return low.upper()
    if low in ROMAN and not first:
        return low.upper()                       # Programming III
    if low in SMALL and not first:
        return low                               # Structures and Algorithms
    return "-".join(_cap(p) for p in low.split("-"))  # Object-Oriented, Newton's


def tidy_name(text: str) -> str:
    words = " ".join((text or "").split()).split(" ")
    if words == [""]:
        return ""
    return " ".join(_word(w, i == 0) for i, w in enumerate(words))
