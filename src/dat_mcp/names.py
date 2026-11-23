"""Keys for matching organizations by name and street address.

scripts/build_db.py stores these keys in fac_filers, and
grantee_identity_resolution computes them for the names and addresses it
looks up, so both sides are normalized the same way.
"""

import re
import unicodedata

# Words that say nothing about which organization it is. A consolidated
# audit adds "and Affiliates" or "and Subsidiaries" to the filer's own name.
IGNORED = {
    "INC", "INCORPORATED", "CORP", "CORPORATION", "LLC", "LTD", "CO", "COMPANY",
    "THE", "OF", "AND", "FOR", "AFFILIATE", "AFFILIATES", "SUBSIDIARY",
    "SUBSIDIARIES",
}  # fmt: skip

STREET_WORDS = {
    "STREET": "ST", "AVENUE": "AVE", "ROAD": "RD", "DRIVE": "DR",
    "BOULEVARD": "BLVD", "HIGHWAY": "HWY", "PARKWAY": "PKWY", "LANE": "LN",
    "PLACE": "PL", "COURT": "CT", "NORTH": "N", "SOUTH": "S", "EAST": "E",
    "WEST": "W",
}  # fmt: skip


# A name shorter than this isn't treated as cut off: sources cut names at a
# field's width (the IRS at 70 characters), not after a few words.
MIN_CUT_OFF = 40


def tokens(name):
    """Every word of a name, upper case, without accents or punctuation.
    An apostrophe joins its word: KINA'OLE is KINAOLE, as the IRS writes it."""
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"['‘’ʻʼ`]", "", text.upper().replace("&", " AND "))
    return re.sub(r"[^A-Z0-9]", " ", text).split()


def cut_off(a, b):
    """Whether one name is the other cut short in the middle of a word:
    "... HEALTH CENTER INCORPORA" and "... Health Center Incorporated".
    Ending at a whole word doesn't count: "Valley College" isn't "Valley
    College Alumni Association" cut short."""
    short, long = sorted((tokens(a), tokens(b)), key=lambda t: len(" ".join(t)))
    last = len(short) - 1
    return (
        len(" ".join(short)) >= MIN_CUT_OFF
        and len(long) > last
        and short[:last] == long[:last]
        and long[last].startswith(short[last])
        and long[last] != short[last]
    )


def words(name):
    """A name's words, upper case, without accents, punctuation, or IGNORED."""
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"['‘’ʻʼ`]", "", text.upper().replace("&", " AND "))
    # A state agency files as "State of Hawaii Department of Health" and
    # registers as "Hawaii Department of Health".
    text = re.sub(r"^\s*STATE OF\s+", "", text)
    return [w for w in re.sub(r"[^A-Z0-9]", " ", text).split() if w not in IGNORED]


def name_key(name):
    """The name's distinct words in alphabetical order, so "County of Union"
    and "UNION COUNTY" share a key. Empty for a name with no words."""
    return " ".join(sorted(set(words(name))))


def address_key(line):
    """A street address's number and next two words, abbreviations made
    uniform: "838 Penn Street, Suite 2" -> "838 PENN ST". Empty unless the
    line starts with a number, so PO boxes and blanks never match."""
    parts = re.sub(r"[^A-Z0-9 ]", " ", (line or "").upper()).split()
    if not parts or not parts[0][0].isdigit():
        return ""
    return " ".join(STREET_WORDS.get(p, p) for p in parts[:3])


def city_key(city):
    """A city name in upper case with punctuation and extra spaces removed:
    "St. Petersburg" and "ST PETERSBURG" share a key."""
    return " ".join(re.sub(r"[^A-Z0-9 ]", " ", (city or "").upper()).split())
