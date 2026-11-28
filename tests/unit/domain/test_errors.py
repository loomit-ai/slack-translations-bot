"""IF-04 · Which model failures are worth one more try, and what an error may say."""

import pytest

from slack_translator.domain.errors import (
    RETRYABLE_MODEL_ERRORS,
    ModelError,
    ModelErrorCategory,
)


@pytest.mark.parametrize(
    ("category", "retryable"),
    [
        (ModelErrorCategory.TIMEOUT, True),
        (ModelErrorCategory.RATE_LIMITED, True),
        (ModelErrorCategory.UNAVAILABLE, True),
        (ModelErrorCategory.INVALID_RESPONSE, True),
        (ModelErrorCategory.REJECTED, False),
    ],
)
def test_an_error_knows_whether_another_try_can_help(
    category: ModelErrorCategory, retryable: bool
) -> None:
    assert ModelError(category, "translate").retryable is retryable
    assert (category in RETRYABLE_MODEL_ERRORS) is retryable


def test_every_category_has_a_fixed_sentence_that_names_no_input() -> None:
    sentences = {str(ModelError(category, "translate")) for category in ModelErrorCategory}

    assert len(sentences) == len(ModelErrorCategory)
