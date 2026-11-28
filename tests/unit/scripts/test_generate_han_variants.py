"""The generator for the Simplified-only and Traditional-only tables (ADR-005)."""

import importlib.util
import unicodedata
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "generate_han_variants.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("generate_han_variants", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def width(line: str) -> int:
    """Display columns, as the line-length check counts them: wide characters take two."""
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in line)


UNIHAN = "\n".join(
    [
        "# Unihan_Variants.txt",
        "# Date: 2099-01-01 00:00:00 GMT [XX]",
        "U+4E1C\tkTraditionalVariant\tU+6771",
        "U+6771\tkSimplifiedVariant\tU+4E1C",
        "U+540E\tkSimplifiedVariant\tU+540E",
        "U+540E\tkTraditionalVariant\tU+540E U+5F8C",
        "U+5F8C\tkSimplifiedVariant\tU+540E",
        "U+5E72\tkTraditionalVariant\tU+4E7E U+5E72 U+5E79",
        "U+4E7E\tkSimplifiedVariant\tU+4E7E U+5E72",
        "U+4E7E\tkTraditionalVariant\tU+4E7E",
        "U+5E79\tkSimplifiedVariant\tU+5E72",
        "U+9879\tkTraditionalVariant\tU+9801<kMeyerWempe:T",
        "U+9801\tkSimplifiedVariant\tU+9879<kMeyerWempe:T",
        "U+4F60\tkSemanticVariant\tU+5C3C",
        "U+4E00\tkTraditionalVariant\tU+4E01",
        "U+4E00\tkSimplifiedVariant\tU+4E02",
        "U+20001\tkTraditionalVariant\tU+20002",
        "U+20002\tkSimplifiedVariant\tU+20001",
    ]
)


def test_a_character_only_one_script_uses_goes_into_that_scripts_table() -> None:
    simplified, traditional = load().parse_variants(UNIHAN)

    assert {"东", "项"} <= simplified
    assert {"東", "頁", "幹"} <= traditional


def test_a_character_that_is_also_a_variant_of_itself_is_in_neither_table() -> None:
    simplified, traditional = load().parse_variants(UNIHAN)

    for shared in ("后", "干", "乾"):
        assert shared not in simplified
        assert shared not in traditional


def test_a_character_with_both_kinds_of_variant_is_in_neither_table() -> None:
    simplified, traditional = load().parse_variants(UNIHAN)

    assert "一" not in simplified
    assert "一" not in traditional


def test_only_the_common_ideograph_block_is_kept() -> None:
    simplified, traditional = load().parse_variants(UNIHAN)

    assert "\U00020001" not in simplified | traditional
    assert "\U00020002" not in simplified | traditional


def test_the_rendered_module_wraps_its_lines_and_names_its_source() -> None:
    module = load()
    simplified, traditional = module.parse_variants(UNIHAN)

    source = module.render_module(simplified, traditional, "Date: 2099-01-01 00:00:00 GMT [XX]")

    assert max(width(line) for line in source.splitlines()) <= 100
    assert "2099-01-01" in source
    namespace: dict[str, object] = {}
    exec(compile(source, "han_variants.py", "exec"), namespace)
    assert namespace["SIMPLIFIED_ONLY"] == frozenset(simplified)
    assert namespace["TRADITIONAL_ONLY"] == frozenset(traditional)
