"""US-13 · The decisions of the local translation page that need no server: where the text goes and
what each failure says."""

import pytest
from tests.support.scripts import load_script

load_script("translation_lab")
page = load_script("translation_page")

CONTEXT = page.Context(
    model="qwen2.5:7b", env_name="local.env", endpoint="localhost:11434/v1", timeout=60.0
)


class TestWhereTheTextGoes:
    @pytest.mark.parametrize("host", ["localhost", "LOCALHOST", "127.0.0.1", "::1", "0.0.0.0"])
    def test_a_model_on_this_computer_keeps_the_text_here(self, host: str) -> None:
        assert page.where_text_goes(host) == "stays on this computer"

    @pytest.mark.parametrize(
        "host",
        [
            "api.openrouter.ai",
            "openrouter.ai",
            "192.168.1.20",
            "host.docker.internal",
            "127.0.0.1.evil.example",
            "localhost.evil.example",
            "notlocalhost",
            "unknown",
        ],
    )
    def test_any_other_host_gets_the_text_and_is_named(self, host: str) -> None:
        assert page.where_text_goes(host) == f"is sent to {host}"


class TestEndpointShown:
    def test_it_shows_host_port_and_path_and_nothing_secret(self) -> None:
        shown = page.endpoint_label("https://user:secret@openrouter.ai:8443/api/v1?key=abc#x")

        assert shown == "openrouter.ai:8443/api/v1"
        assert "secret" not in shown and "abc" not in shown

    def test_a_plain_local_address(self) -> None:
        assert page.endpoint_label("http://localhost:11434/v1") == "localhost:11434/v1"
        assert page.endpoint_label("http://127.0.0.1:8089") == "127.0.0.1:8089"


class TestWhatEachFailureSays:
    @pytest.mark.parametrize(
        ("category", "status", "kind"),
        [
            ("unavailable", None, "unreachable"),
            ("timeout", None, "timeout"),
            ("rejected", 404, "model_not_found"),
            ("rejected", 401, "key_refused"),
            ("rejected", 403, "key_refused"),
            ("rejected", 400, "rejected"),
            ("rejected", 422, "rejected"),
            ("rate_limited", 429, "rate_limited"),
            ("unavailable", 500, "server_error"),
            ("unavailable", 503, "server_error"),
            ("invalid_response", 200, "bad_answer"),
            ("something_new", None, "failed"),
        ],
    )
    def test_each_failure_has_its_own_kind(
        self, category: str, status: int | None, kind: str
    ) -> None:
        assert page.explain_failure(category, status, CONTEXT).kind == kind

    @pytest.mark.parametrize(
        ("category", "status"),
        [
            ("unavailable", None),
            ("timeout", None),
            ("rejected", 404),
            ("rejected", 401),
            ("rate_limited", 429),
            ("unavailable", 500),
            ("invalid_response", 200),
            ("something_new", None),
        ],
    )
    def test_each_failure_says_what_happened_and_what_to_do_next(
        self, category: str, status: int | None
    ) -> None:
        problem = page.explain_failure(category, status, CONTEXT)

        assert problem.title.strip() and problem.advice.strip()
        assert problem.title != problem.advice

    def test_the_advice_names_the_file_to_fix_and_the_model_and_endpoint_involved(self) -> None:
        unreachable = page.explain_failure("unavailable", None, CONTEXT)
        timeout = page.explain_failure("timeout", None, CONTEXT)
        missing = page.explain_failure("rejected", 404, CONTEXT)

        assert "local.env" in unreachable.advice and "localhost:11434/v1" in unreachable.advice
        assert "local.env" in timeout.advice and "60" in timeout.advice
        assert "qwen2.5:7b" in missing.title + missing.advice
        assert "TRANSLATOR_MODEL" in missing.advice

    def test_a_server_error_shows_the_http_status_the_server_gave(self) -> None:
        assert "HTTP 500" in page.explain_failure("unavailable", 500, CONTEXT).title

    def test_the_empty_input_problem_asks_for_a_message(self) -> None:
        problem = page.empty_input()

        assert problem.kind == "empty"
        assert "message" in problem.title.lower() + problem.advice.lower()
