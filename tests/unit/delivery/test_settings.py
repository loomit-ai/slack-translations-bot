"""FF-08: every CFG rule has an accept case and a reject case; secrets never show in errors."""

from collections.abc import Mapping

import pytest
from pydantic import ValidationError

from slack_translator.delivery.settings import Settings, invalid_keys

SECRETS = {
    "SLACK_BOT_TOKEN": "xoxb-test-secret-bot",
    "SLACK_APP_TOKEN": "xapp-test-secret-app",
    "TRANSLATOR_API_KEY": "test-secret-translator-key",
}

VALID: Mapping[str, str] = {
    "CHANNEL_ALLOWLIST": "C0123ABCDE",
    "TRANSLATOR_BASE_URL": "https://translator.example.test/v1",
    "TRANSLATOR_MODEL": "test-model",
    "APP_ENV": "development",
    **SECRETS,
}


def build(**overrides: str | None) -> Settings:
    env = dict(VALID)
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return Settings.from_environ(env)


def rejected_keys(**overrides: str | None) -> list[str]:
    with pytest.raises(ValidationError) as caught:
        build(**overrides)
    return invalid_keys(caught.value)


def test_minimal_environment_gets_the_documented_defaults() -> None:
    settings = build()
    assert [lang.code for lang in settings.language_seed] == ["en", "es", "ru", "ko", "vi", "zh"]
    assert settings.read_language == "en"
    assert settings.classifier_min_confidence == 0.7
    assert settings.classifier_timeout == 3
    assert settings.translator_timeout == 20
    assert settings.slack_mode == "socket"
    assert settings.log_level == "INFO"
    assert settings.port == 8000
    assert settings.translator_extra_body == {}


def test_empty_values_count_as_unset() -> None:
    assert build(PORT="", LOG_LEVEL="").port == 8000
    assert rejected_keys(TRANSLATOR_MODEL="") == ["TRANSLATOR_MODEL"]


REQUIRED_KEYS = [
    "CHANNEL_ALLOWLIST",
    "TRANSLATOR_BASE_URL",
    "TRANSLATOR_MODEL",
    "APP_ENV",
    *SECRETS,
]


@pytest.mark.parametrize("key", REQUIRED_KEYS)
def test_every_required_key_is_reported_by_name_when_missing(key: str) -> None:
    assert rejected_keys(**{key: None}) == [key]


def test_all_problems_are_reported_together() -> None:
    assert rejected_keys(PORT="80", TRANSLATOR_TIMEOUT="0") == ["TRANSLATOR_TIMEOUT", "PORT"]


class TestChannelAllowlist:
    def test_accepts_public_and_private_ids_and_removes_duplicates(self) -> None:
        settings = build(CHANNEL_ALLOWLIST="C0123ABCDE, G0123ABCDE,C0123ABCDE")
        assert settings.channel_allowlist == ("C0123ABCDE", "G0123ABCDE")

    @pytest.mark.parametrize("raw", ["C0123", "D0123ABCDE", "c0123abcde", "C0123ABCDE,oops"])
    def test_rejects_bad_ids(self, raw: str) -> None:
        assert rejected_keys(CHANNEL_ALLOWLIST=raw) == ["CHANNEL_ALLOWLIST"]

    @pytest.mark.parametrize("env", ["development", "staging"])
    @pytest.mark.parametrize("raw", [None, ""])
    def test_an_empty_list_is_refused_where_a_test_build_could_read_a_real_channel(
        self, env: str, raw: str | None
    ) -> None:
        assert rejected_keys(APP_ENV=env, CHANNEL_ALLOWLIST=raw) == ["CHANNEL_ALLOWLIST"]

    @pytest.mark.parametrize("raw", [None, ""])
    def test_production_may_run_without_a_list_and_then_membership_decides(
        self, raw: str | None
    ) -> None:
        assert build(APP_ENV="production", CHANNEL_ALLOWLIST=raw).channel_allowlist == ()

    def test_production_may_still_narrow_the_bot_to_a_few_channels(self) -> None:
        settings = build(APP_ENV="production", CHANNEL_ALLOWLIST="C0123ABCDE")
        assert settings.channel_allowlist == ("C0123ABCDE",)


class TestLanguageSeed:
    def test_accepts_a_custom_seed_that_includes_the_read_language(self) -> None:
        settings = build(LANGUAGE_SEED="en:team,ja:customer")
        assert [lang.code for lang in settings.language_seed] == ["en", "ja"]

    @pytest.mark.parametrize("raw", ["en:team,xx:customer", "en:team,en:customer", "en"])
    def test_rejects_bad_seeds(self, raw: str) -> None:
        assert rejected_keys(LANGUAGE_SEED=raw) == ["LANGUAGE_SEED"]


class TestReadLanguage:
    def test_accepts_another_team_language(self) -> None:
        assert build(READ_LANGUAGE="es").read_language == "es"

    @pytest.mark.parametrize("raw", ["ko", "fr", "english"])
    def test_rejects_a_customer_or_missing_language(self, raw: str) -> None:
        assert rejected_keys(READ_LANGUAGE=raw) == ["READ_LANGUAGE"]

    def test_the_default_must_also_be_a_team_language_in_the_seed(self) -> None:
        assert rejected_keys(LANGUAGE_SEED="ko:customer,ja:customer") == ["READ_LANGUAGE"]


class TestClassifierSettings:
    @pytest.mark.parametrize("raw", ["0.5", "0.7", "1"])
    def test_accepts_a_minimum_lead_from_half_to_one(self, raw: str) -> None:
        assert build(CLASSIFIER_MIN_CONFIDENCE=raw).classifier_min_confidence == float(raw)

    @pytest.mark.parametrize("raw", ["0.49", "0", "-0.1", "1.01", "high"])
    def test_rejects_a_minimum_lead_outside_the_range(self, raw: str) -> None:
        assert rejected_keys(CLASSIFIER_MIN_CONFIDENCE=raw) == ["CLASSIFIER_MIN_CONFIDENCE"]

    @pytest.mark.parametrize("raw", ["0.1", "10"])
    def test_accepts_timeouts_inside_the_range(self, raw: str) -> None:
        assert build(CLASSIFIER_TIMEOUT=raw).classifier_timeout == float(raw)

    @pytest.mark.parametrize("raw", ["0.05", "10.5"])
    def test_rejects_timeouts_outside_the_range(self, raw: str) -> None:
        assert rejected_keys(CLASSIFIER_TIMEOUT=raw) == ["CLASSIFIER_TIMEOUT"]


class TestTranslatorSettings:
    @pytest.mark.parametrize("raw", ["1", "60"])
    def test_accepts_timeouts_inside_the_range(self, raw: str) -> None:
        assert build(TRANSLATOR_TIMEOUT=raw).translator_timeout == float(raw)

    @pytest.mark.parametrize("raw", ["0.5", "61"])
    def test_rejects_timeouts_outside_the_range(self, raw: str) -> None:
        assert rejected_keys(TRANSLATOR_TIMEOUT=raw) == ["TRANSLATOR_TIMEOUT"]

    @pytest.mark.parametrize(
        "raw",
        [
            "https://openrouter.example.test/api/v1",
            "http://localhost:9000/v1",
            "http://127.0.0.1:9000",
        ],
    )
    def test_accepts_https_and_plain_http_only_for_local_fakes(self, raw: str) -> None:
        assert build(TRANSLATOR_BASE_URL=raw).translator_base_url == raw

    @pytest.mark.parametrize(
        "raw",
        [
            "http://translator.example.test/v1",
            "https://translator.example.test/v1/",
            "translator.example.test",
            "ftp://translator.example.test",
        ],
    )
    def test_rejects_insecure_or_malformed_urls(self, raw: str) -> None:
        assert rejected_keys(TRANSLATOR_BASE_URL=raw) == ["TRANSLATOR_BASE_URL"]

    def test_extra_body_accepts_a_json_object(self) -> None:
        settings = build(TRANSLATOR_EXTRA_BODY='{"provider": {"order": ["x"]}}')
        assert settings.translator_extra_body == {"provider": {"order": ["x"]}}

    @pytest.mark.parametrize("raw", ["[1]", "not json", '"text"'])
    def test_extra_body_rejects_anything_but_an_object(self, raw: str) -> None:
        assert rejected_keys(TRANSLATOR_EXTRA_BODY=raw) == ["TRANSLATOR_EXTRA_BODY"]


class TestSlackSettings:
    def test_accepts_tokens_with_the_right_prefix(self) -> None:
        settings = build()
        assert settings.slack_bot_token.get_secret_value() == SECRETS["SLACK_BOT_TOKEN"]
        assert settings.slack_app_token.get_secret_value() == SECRETS["SLACK_APP_TOKEN"]

    def test_rejects_swapped_tokens(self) -> None:
        keys = rejected_keys(
            SLACK_BOT_TOKEN=SECRETS["SLACK_APP_TOKEN"], SLACK_APP_TOKEN=SECRETS["SLACK_BOT_TOKEN"]
        )
        assert keys == ["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"]

    def test_http_mode_is_not_accepted_before_milestone_0_6(self) -> None:
        assert rejected_keys(SLACK_MODE="http") == ["SLACK_MODE"]
        assert build(SLACK_MODE="socket").slack_mode == "socket"


class TestRuntimeSettings:
    @pytest.mark.parametrize("raw", ["development", "staging", "production"])
    def test_accepts_the_three_environments(self, raw: str) -> None:
        assert build(APP_ENV=raw).app_env == raw

    def test_rejects_an_unknown_environment(self) -> None:
        assert rejected_keys(APP_ENV="test") == ["APP_ENV"]

    @pytest.mark.parametrize("raw", ["INFO", "WARNING", "ERROR"])
    def test_accepts_log_levels_in_every_environment(self, raw: str) -> None:
        assert build(APP_ENV="production", LOG_LEVEL=raw).log_level == raw

    def test_debug_is_accepted_only_in_development(self) -> None:
        assert build(APP_ENV="development", LOG_LEVEL="DEBUG").log_level == "DEBUG"
        assert rejected_keys(APP_ENV="staging", LOG_LEVEL="DEBUG") == ["LOG_LEVEL"]
        assert rejected_keys(APP_ENV="production", LOG_LEVEL="DEBUG") == ["LOG_LEVEL"]

    def test_rejects_unknown_log_levels(self) -> None:
        assert rejected_keys(LOG_LEVEL="TRACE") == ["LOG_LEVEL"]

    @pytest.mark.parametrize("raw", ["1024", "8000", "65535"])
    def test_accepts_ports_in_range(self, raw: str) -> None:
        assert build(PORT=raw).port == int(raw)

    @pytest.mark.parametrize("raw", ["1023", "65536", "eight"])
    def test_rejects_ports_out_of_range(self, raw: str) -> None:
        assert rejected_keys(PORT=raw) == ["PORT"]


class TestMemberEmailDomains:
    def test_the_default_is_the_two_x3m_domains(self) -> None:
        assert build().member_email_domains == frozenset({"x3mads.com", "loomit.ai"})

    def test_values_are_trimmed_lower_cased_and_deduplicated(self) -> None:
        settings = build(MEMBER_EMAIL_DOMAINS=" X3MADS.com, loomit.AI ,x3mads.com,partner.example")
        assert settings.member_email_domains == frozenset(
            {"x3mads.com", "loomit.ai", "partner.example"}
        )

    @pytest.mark.parametrize(
        "raw",
        [
            "x3mads",
            "@x3mads.com",
            "ana@x3mads.com",
            "x3mads..com",
            "-x3mads.com",
            "x3mads.com/path",
            "x3mads.com,",
            "x3mads.com loomit.ai",
        ],
    )
    def test_rejects_anything_that_is_not_a_plain_domain(self, raw: str) -> None:
        assert rejected_keys(MEMBER_EMAIL_DOMAINS=raw) == ["MEMBER_EMAIL_DOMAINS"]


def test_reserved_and_unknown_keys_are_ignored() -> None:
    settings = build(TOKENS_URI="s3://bucket/tokens.json", SOMETHING_ELSE="x")
    assert settings.port == 8000


def test_secret_values_never_appear_in_an_error_or_in_the_settings_text() -> None:
    with pytest.raises(ValidationError) as caught:
        build(SLACK_BOT_TOKEN="not-a-bot-token-SECRETMARKER", PORT="80")
    text = f"{caught.value}\n{caught.value!r}\n{invalid_keys(caught.value)}"
    assert "SECRETMARKER" not in text
    ok = build()
    for value in SECRETS.values():
        assert value not in f"{ok!r} {ok}"
        assert value not in ok.model_dump_json()
