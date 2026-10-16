"""Section rendering and pagination (``searchfetch.compose``)."""

import pytest
from conftest import load_fixture

from searchfetch.compose import (
    NO_CONTENT,
    compose_search_results,
    compose_sections,
    field,
    paginate,
)

COMPOSITION_CASES = load_fixture("composition.json")


class TestSharedComposition:
    @pytest.mark.parametrize("case", COMPOSITION_CASES, ids=lambda case: case["name"])
    def test_matches_expected(self, case):
        assert compose_sections(case["sections"]) == case["expected"]

    def test_fixture_covers_all_section_types(self):
        types = {s["type"] for case in COMPOSITION_CASES for s in case["sections"]}
        assert types == {"value", "list", "group", "records"}


class TestComposeSections:
    def test_empty_input(self):
        assert compose_sections([]) == NO_CONTENT == "(No content extracted from this page.)"

    def test_all_empty_sections(self):
        sections = [
            {"name": "A", "type": "value", "value": "  "},
            {"name": "B", "type": "list", "items": ["", " "]},
            {"name": "C", "type": "group", "fields": {"x": ""}},
            {"name": "D", "type": "records", "items": [{"x": ""}]},
        ]
        assert compose_sections(sections) == NO_CONTENT

    def test_threaded_records_without_author_render_the_comment_only(self):
        sections = [
            {
                "name": "C",
                "type": "records",
                "items": [{"Author": "A", "Comment": "one"}, {"Comment": "two"}],
            }
        ]
        assert compose_sections(sections) == "## C\n\n**A:**\n\none\n\n---\n\ntwo"

    def test_unknown_type(self):
        with pytest.raises(ValueError, match="Unknown section type 'search_results'"):
            compose_sections([{"name": "x", "type": "search_results", "items": []}])


class TestField:
    def test_case_insensitive_first_non_empty_key(self):
        record = {"AUTHOR": "", "User": " bob ", "Body": "text"}
        assert field(record, "author", "user") == "bob"
        assert field(record, "comment", "body") == "text"

    def test_missing(self):
        assert field({"a": "x"}, "b") == ""

    def test_non_string_values_are_stringified(self):
        assert field({"n": 5, "none": None}, "n") == "5"
        assert field({"none": None}, "none") == ""


def results(*items):
    return {"name": "Results", "type": "records", "items": list(items)}


class TestComposeSearchResults:
    def test_numbered_results(self):
        output = compose_search_results(
            results(
                {"Title": "Test Page", "URL": "https://example.com", "Snippet": "A description"},
                {"title": "Second", "url": "https://example.org/x"},
            )
        )
        assert output == (
            "## Results\n\n"
            "[1] Test Page\n    URL: https://example.com\n    Snippet: A description\n\n"
            "[2] Second\n    URL: https://example.org/x"
        )

    @pytest.mark.parametrize(
        "url",
        [
            "/internal",
            "javascript:alert(1)",
            "ftp://example.com/file",
            "https://www.google.com/search?q=test",
            "https://google.com/search?q=test",
            "https://support.google.com/websearch/answer/1",
            "http://",
            "http://[::1",
        ],
    )
    def test_unusable_urls_are_dropped_but_the_result_is_kept(self, url):
        output = compose_search_results(results({"Title": "T", "URL": url, "Snippet": "s"}))
        assert output == "## Results\n\n[1] T\n    Snippet: s"

    def test_other_google_pages_are_kept(self):
        url = "https://www.google.com/maps/place/x"
        assert f"URL: {url}" in compose_search_results(results({"Title": "T", "URL": url}))

    def test_untitled_results_are_skipped_without_gaps_in_numbering(self):
        output = compose_search_results(
            results(
                {"Title": "", "URL": "https://a.com"}, {"Title": "Kept"}, {"URL": "https://b.com"}
            )
        )
        assert output == "## Results\n\n[1] Kept"

    @pytest.mark.parametrize("items", [[], [{"Title": "", "URL": ""}]])
    def test_no_usable_results(self, items):
        assert compose_search_results(results(*items)) == NO_CONTENT


class TestPaginate:
    TEXT = "a😀b😀c"

    def test_slices_by_code_points(self):
        assert paginate(self.TEXT, "raw", 1, 2) == (
            "😀b\n\n---\n"
            '[webfetch: template="raw", showing characters 1 to 3 of 5 total. '
            "Use start_index=3 to read more.]"
        )

    def test_last_page_has_no_continuation_hint(self):
        output = paginate(self.TEXT, "raw", 3, 10)
        assert output.startswith("😀c\n\n---\n")
        assert output.endswith("showing characters 3 to 5 of 5 total.]")

    def test_whole_text(self):
        assert paginate("abc", "t", 0, 3).endswith("showing characters 0 to 3 of 3 total.]")

    def test_start_beyond_the_end_is_clamped(self):
        output = paginate("abc", "t", 99, 10)
        assert output.startswith("\n\n---\n")
        assert "showing characters 3 to 3 of 3 total.]" in output

    def test_empty_text(self):
        assert "showing characters 0 to 0 of 0 total.]" in paginate("", "t", 0, 10)

    def test_template_name_in_footer(self):
        assert 'template="auto (fallback)"' in paginate("x", "auto (fallback)", 0, 10)

    def test_pages_chain_through_start_index(self):
        text = "0123456789"
        first = paginate(text, "t", 0, 4)
        assert first.startswith("0123\n") and "Use start_index=4 to read more." in first
        assert paginate(text, "t", 4, 4).startswith("4567\n")
        assert paginate(text, "t", 8, 4).startswith("89\n")
