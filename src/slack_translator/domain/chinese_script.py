"""ADR-005 · Simplified or Traditional Chinese, decided from the characters of the text.

Lingua has one Chinese entry and does not tell the scripts apart. This counts the characters that
only Simplified uses against those only Traditional uses (tables generated from the Unicode Han
database, see `han_variants`). A character valid in both scripts counts for neither. If neither
script has more characters than the other, the script is not decided and the answer is `None`.
"""

from slack_translator.domain.han_variants import SIMPLIFIED_ONLY, TRADITIONAL_ONLY
from slack_translator.domain.languages import Script


def script_of(text: str) -> Script | None:
    simplified = sum(1 for char in text if char in SIMPLIFIED_ONLY)
    traditional = sum(1 for char in text if char in TRADITIONAL_ONLY)
    if simplified > traditional:
        return "Hans"
    if traditional > simplified:
        return "Hant"
    return None
