"""Deterministic extraction of spoken figures from narration text.

The scripts are written for text-to-speech, so numbers normally appear as
words.  This module converts only explicit, locally verifiable number phrases;
it never estimates or invents a value.  Character offsets are retained for
timed overlays and captions.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass


_SMALL = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_SCALES = {
    "hundred": 100,
    "thousand": 1_000,
    "million": 1_000_000,
    "billion": 1_000_000_000,
    "trillion": 1_000_000_000_000,
}
_NUMBER_WORDS = set(_SMALL) | set(_TENS) | set(_SCALES) | {
    "and", "point", "half", "quarter", "a",
}
_UNIT_WORDS = {
    "pound": "GBP", "pounds": "GBP", "sterling": "GBP",
    "percent": "percent", "percentage": "percent",
    "dollar": "USD", "dollars": "USD",
    "euro": "EUR", "euros": "EUR",
    "pence": "pence", "penny": "pence",
}
_COUNT_UNITS = {
    "application", "applications", "bank", "banks", "branch", "branches",
    "business", "businesses", "case", "cases", "company", "companies",
    "customer", "customers", "day", "days", "employee", "employees",
    "home", "homes", "item", "items", "job", "jobs", "month", "months",
    "packet", "packets", "people", "person", "property", "properties",
    "shop", "shops", "store", "stores", "transaction", "transactions",
    "week", "weeks", "worker", "workers", "year", "years",
}
_TOKEN_RE = re.compile(r"£?\d[\d,]*(?:\.\d+)?%?|[A-Za-z]+(?:-[A-Za-z]+)?")


@dataclass(frozen=True)
class Figure:
    start: int
    end: int
    raw: str
    value: float
    unit: str
    display: str
    is_year: bool = False
    unit_label: str = ""

    @property
    def significance(self) -> float:
        magnitude = math.log10(max(abs(self.value), 1.0))
        if self.unit in {"GBP", "USD", "EUR"}:
            return 80.0 + magnitude * 8.0
        if self.unit == "percent":
            return 62.0 + magnitude * 7.0
        if self.unit == "pence":
            return 38.0 + magnitude * 5.0
        if self.is_year:
            return 8.0
        return 20.0 + magnitude * 8.0


def _words(token: str) -> list[str]:
    return token.lower().replace("-", " ").split()


def _is_number_token(token: str) -> bool:
    lower = token.lower()
    if re.fullmatch(r"£?\d[\d,]*(?:\.\d+)?%?", token):
        return True
    return all(part in _NUMBER_WORDS for part in _words(lower))


def _parse_integer_words(words: list[str]) -> float | None:
    """Parse a conventional English cardinal phrase."""
    if not words:
        return None
    total = 0.0
    current = 0.0
    saw_number = False
    for word in words:
        if word in {"and", "a"}:
            continue
        if word in _SMALL:
            current += _SMALL[word]
            saw_number = True
        elif word in _TENS:
            current += _TENS[word]
            saw_number = True
        elif word == "hundred":
            current = max(current, 1.0) * 100.0
            saw_number = True
        elif word in {"thousand", "million", "billion", "trillion"}:
            total += max(current, 1.0) * _SCALES[word]
            current = 0.0
            saw_number = True
        else:
            return None
    return total + current if saw_number else None


def _parse_word_number(words: list[str]) -> tuple[float, bool] | None:
    words = [part for word in words for part in _words(word)]
    if not words:
        return None

    # Spoken years such as "twenty twenty five" and "nineteen ninety nine".
    if (
        len(words) >= 2
        and words[0] in {"nineteen", "twenty"}
        and (
            words[1] in _TENS
            or _SMALL.get(words[1], -1) >= 10
        )
        and not any(w in _SCALES or w == "point" for w in words[1:])
    ):
        tail = _parse_integer_words(words[1:])
        prefix = _SMALL.get(words[0], _TENS.get(words[0]))
        if tail is not None and prefix in {19, 20} and 0 <= tail <= 99:
            value = float(prefix * 100 + int(tail))
            if 1800 <= value <= 2099:
                return value, True

    # "four and a half thousand" / "two and a quarter million".
    fraction = 0.0
    fraction_at = None
    for marker, amount in (("half", 0.5), ("quarter", 0.25)):
        if marker in words:
            pos = words.index(marker)
            if pos >= 1 and words[pos - 1] in {"a", "one"}:
                fraction = amount
                fraction_at = pos - 2 if pos >= 2 and words[pos - 2] == "and" else pos - 1
                break
    if fraction_at is not None:
        before = [w for w in words[:fraction_at] if w != "and"]
        after = words[words.index("half") + 1:] if "half" in words else words[words.index("quarter") + 1:]
        base = _parse_integer_words(before) if before else 0.0
        if base is None:
            return None
        multiplier = 1.0
        if after:
            if len(after) != 1 or after[0] not in _SCALES:
                return None
            multiplier = float(_SCALES[after[0]])
        return (base + fraction) * multiplier, False

    if "point" in words:
        point = words.index("point")
        whole = _parse_integer_words(words[:point])
        decimal_words = [w for w in words[point + 1:] if w != "and"]
        trailing_scale = 1.0
        if decimal_words and decimal_words[-1] in _SCALES:
            trailing_scale = float(_SCALES[decimal_words.pop()])
        digits: list[str] = []
        for word in decimal_words:
            if word not in _SMALL or _SMALL[word] > 9:
                return None
            digits.append(str(_SMALL[word]))
        if whole is None or not digits:
            return None
        return (whole + float("0." + "".join(digits))) * trailing_scale, False

    value = _parse_integer_words(words)
    if value is None:
        return None
    return value, False


def _format_number(value: float) -> str:
    if math.isclose(value, round(value), abs_tol=1e-9):
        return f"{int(round(value)):,}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _singular(word: str) -> str:
    if word.endswith("ies") and len(word) > 3:
        return word[:-3] + "y"
    if word.endswith("ches") or word.endswith("shes"):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _display(value: float, unit: str, is_year: bool) -> str:
    if is_year:
        return str(int(round(value)))
    if unit == "GBP":
        return "£" + _format_number(value)
    if unit == "USD":
        return "$" + _format_number(value)
    if unit == "EUR":
        return "€" + _format_number(value)
    if unit == "percent":
        return _format_number(value) + "%"
    if unit == "pence":
        return _format_number(value) + "p"
    return _format_number(value)


def extract_figures(text: str) -> list[Figure]:
    """Return explicit figures in source order, with exact source spans."""
    tokens = list(_TOKEN_RE.finditer(text))
    figures: list[Figure] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if not _is_number_token(token.group()):
            i += 1
            continue

        raw_token = token.group()
        numeric_match = re.fullmatch(r"(£)?([\d,]+(?:\.\d+)?)(%)?", raw_token)
        is_year = False
        if numeric_match:
            value = float(numeric_match.group(2).replace(",", ""))
            unit = "GBP" if numeric_match.group(1) else (
                "percent" if numeric_match.group(3) else "number"
            )
            end_i = i
        else:
            end_i = i
            phrase_words: list[str] = []
            while end_i < len(tokens) and _is_number_token(tokens[end_i].group()):
                if end_i > i:
                    gap = text[tokens[end_i - 1].end():tokens[end_i].start()]
                    if not re.fullmatch(r"[\s-]*", gap):
                        break
                candidate = tokens[end_i].group().lower()
                # "and" or "a" only belongs to the number if another numeric
                # word follows; otherwise it starts ordinary prose.
                if candidate in {"and", "a"}:
                    if end_i + 1 >= len(tokens) or not _is_number_token(tokens[end_i + 1].group()):
                        break
                phrase_words.append(candidate)
                end_i += 1
            end_i -= 1
            parsed = _parse_word_number(phrase_words)
            if parsed is None:
                i += 1
                continue
            value, is_year = parsed
            unit = "number"

        unit_i = end_i + 1
        unit_label = ""
        if unit_i < len(tokens):
            next_word = tokens[unit_i].group().lower()
            if next_word == "per" and unit_i + 1 < len(tokens):
                after = tokens[unit_i + 1].group().lower()
                if after == "cent":
                    unit = "percent"
                    end_i = unit_i + 1
            elif next_word in _UNIT_WORDS:
                unit = _UNIT_WORDS[next_word]
                end_i = unit_i
            elif next_word in _COUNT_UNITS:
                unit_label = next_word
                unit = f"count:{_singular(next_word)}"
                end_i = unit_i

        # A four-digit value without an explicit unit is a year only when it is
        # plausibly a calendar year. Explicit currency/count units win.
        if unit != "number":
            is_year = False
        elif is_year and 1800 <= value <= 2099 and math.isclose(value, round(value)):
            is_year = True
            unit = "year"

        start = token.start()
        end = tokens[end_i].end()
        raw = text[start:end]
        figures.append(Figure(
            start=start,
            end=end,
            raw=raw,
            value=value,
            unit=unit,
            display=_display(value, unit, is_year),
            is_year=is_year,
            unit_label=unit_label,
        ))
        i = end_i + 1
    return figures


def choose_significant_figure(text: str) -> Figure | None:
    """Choose one strong overlay/selection hook, preferring money and rates."""
    figures = extract_figures(text)
    return max(figures, key=lambda f: (f.significance, -f.start), default=None)
