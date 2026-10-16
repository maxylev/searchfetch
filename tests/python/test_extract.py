"""Template-driven extraction (``searchfetch.extract``)."""

import pytest
from bs4 import BeautifulSoup
from conftest import load_fixture

from searchfetch.compose import compose_sections
from searchfetch.extract import (
    DEFAULT_REMOVE,
    apply_transforms,
    clean_document,
    extract_section,
    extract_template,
    extract_value,
    select_in_scope,
    split_selectors,
)
from searchfetch.templates import BUILTIN_TEMPLATES

EXTRACTION_CASES = load_fixture("extraction.json")
TEMPLATE_CASES = load_fixture("templates.json")


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


class TestSplitSelectors:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("", [""]),
            ("a", ["a"]),
            ("a, b ,c", ["a", "b", "c"]),
            (".missing, ", [".missing", ""]),
            (":is(p, h3), span", [":is(p, h3)", "span"]),
            ("[data-x='a,b'], p", ["[data-x='a,b']", "p"]),
            ('a[title="x,y"], b', ['a[title="x,y"]', "b"]),
            (r"a\,b, c", [r"a\,b", "c"]),
            ('a[data-x="it\'s, ok"], b', ['a[data-x="it\'s, ok"]', "b"]),
        ],
    )
    def test_splits_on_top_level_commas_only(self, value, expected):
        assert split_selectors(value) == expected

    def test_default_is_one_empty_fallback(self):
        assert split_selectors() == [""]


class TestSharedExtractionFixtures:
    @pytest.mark.parametrize("case", EXTRACTION_CASES, ids=lambda case: case["name"])
    def test_records_match_expected(self, case):
        template = {
            "remove": [],
            "sections": [
                {
                    "name": "Results",
                    "selector": case["selector"],
                    "multiple": True,
                    "children": case["children"],
                }
            ],
        }
        [result] = extract_template(soup(case["html"]), template)
        assert result["type"] == "records"
        assert result["items"] == case["expected"]


class TestCleanDocument:
    def test_default_rules_remove_noise(self):
        doc = soup(
            "<body><script>x</script><style>y</style><nav>n</nav><footer>f</footer>"
            "<noscript>z</noscript><iframe></iframe><svg></svg><div class='advertisement'>ad</div>"
            "<p>keep</p></body>"
        )
        clean_document(doc)
        assert doc.get_text() == "keep"

    def test_custom_rules_replace_defaults(self):
        doc = soup("<nav>n</nav><aside>a</aside>")
        clean_document(doc, ["aside"])
        assert doc.nav is not None
        assert doc.aside is None

    def test_strips_inline_styles_and_data_uris(self):
        doc = soup(
            "<p style='color:red'>t</p><img src='data:image/png;base64,AAAA'><img src='/a.png'>"
        )
        clean_document(doc, [])
        assert doc.p.get("style") is None
        first, second = doc.find_all("img")
        assert first.get("src") is None
        assert second["src"] == "/a.png"

    def test_unsupported_selectors_are_skipped(self):
        doc = soup("<nav>n</nav><p>keep</p>")
        clean_document(doc, ["p:nonexistent-pseudo(", "nav"])
        assert doc.nav is None
        assert doc.p is not None

    def test_default_remove_is_exposed(self):
        assert "script" in DEFAULT_REMOVE


class TestSelectInScope:
    def test_prefers_descendants(self):
        doc = soup("<div class='r'><p>in</p></div><p>out</p>")
        assert [t.get_text() for t in select_in_scope(doc.select_one(".r"), "p")] == ["in"]

    def test_matches_scope_itself_and_ancestors(self):
        doc = soup("<section class='s'><div class='r'><b>x</b></div></section>")
        scope = doc.select_one("b")
        assert select_in_scope(scope, "b") == [scope]
        assert select_in_scope(scope, ".s") == [doc.select_one(".s")]

    def test_never_borrows_from_siblings(self):
        doc = soup("<div class='r'><h3>A</h3></div><div class='r'><h3>B</h3><p>only B</p></div>")
        assert select_in_scope(doc.select(".r")[0], "p") == []

    def test_fallbacks_and_empty_selector(self):
        doc = soup("<div class='r'><span>s</span></div>")
        scope = doc.select_one(".r")
        assert select_in_scope(scope, ".nope, span") == [doc.span]
        assert select_in_scope(scope, ".nope, ") == [scope]
        assert select_in_scope(scope, "") == [scope]
        assert select_in_scope(scope, ".nope") == []


class TestTransforms:
    @pytest.mark.parametrize(
        ("value", "transform", "page_url", "expected"),
        [
            ("  hello  ", "strip", "", "hello"),
            ("  hello  ", ["strip", "strip"], "", "hello"),
            ("x", None, "", "x"),
            ("x", [], "", "x"),
            (
                "/url?q=https://example.com/page&sa=U",
                "decode_google_url",
                "",
                "https://example.com/page",
            ),
            ("https://example.com/page", "decode_google_url", "", "https://example.com/page"),
            ("/url?sa=U", "decode_google_url", "", "/url?sa=U"),
            (
                "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa%252Fb%3Fq%3Da%2526b&rut=1",
                "decode_ddg_url",
                "",
                "https://example.com/a%2Fb?q=a%26b",
            ),
            ("https://example.com/", "decode_ddg_url", "", "https://example.com/"),
            ("/docs/api", "resolve_href", "https://example.com", "https://example.com/docs/api"),
            (
                "../other",
                "resolve_href",
                "https://example.com/docs/page",
                "https://example.com/other",
            ),
            ("https://other.com/p", "resolve_href", "https://example.com", "https://other.com/p"),
            ("/docs", "resolve_href", "", "/docs"),
            ('{"a": 1}', "json_parse", "", '{\n  "a": 1\n}'),
            ('{"k": "é"}', "json_parse", "", '{\n  "k": "é"\n}'),
            ("not json", "json_parse", "", "not json"),
            ("", "resolve_href", "https://example.com", ""),
        ],
    )
    def test_transform(self, value, transform, page_url, expected):
        assert apply_transforms(value, transform, page_url) == expected

    def test_chain_applies_in_order(self):
        value = apply_transforms("  /url?q=https://e.com/a  ", ["strip", "decode_google_url"])
        assert value == "https://e.com/a"

    def test_chain_stops_once_empty(self):
        assert apply_transforms("   ", ["strip", "resolve_href"], "https://e.com") == ""


class TestExtractValue:
    def element(self, html="<div class='a b' data-x='1'> Hello <b>big</b>\n world </div>"):
        return soup(html).div

    def test_text_collapses_whitespace(self):
        assert extract_value(self.element(), {}) == "Hello big world"

    def test_markdown(self):
        assert extract_value(self.element(), {"format": "markdown"}) == "Hello **big**\nworld"

    def test_html(self):
        assert extract_value(self.element(), {"format": "html"}) == "Hello <b>big</b>\n world"

    def test_attribute(self):
        section = {"format": "attribute", "attribute": "data-x"}
        assert extract_value(self.element(), section) == "1"

    def test_multi_valued_attribute_is_joined(self):
        assert extract_value(self.element(), {"format": "attribute", "attribute": "class"}) == "a b"

    def test_missing_attribute_is_empty(self):
        assert extract_value(self.element(), {"format": "attribute", "attribute": "href"}) == ""

    def test_transform_receives_page_url(self):
        el = soup("<a href='/x'>t</a>").a
        section = {"format": "attribute", "attribute": "href", "transform": "resolve_href"}
        assert extract_value(el, section, "https://e.com/a/") == "https://e.com/x"


DOC = soup(
    "<html><body><h1> Title </h1><ul><li>a</li><li>b</li><li>c</li></ul>"
    "<div class='card'><h2>One</h2><a href='/1'>l</a></div>"
    "<div class='card'><a href='/2'>l</a></div>"
    "<div class='card'><p>nothing useful</p></div></body></html>"
)

CHILDREN = [
    {"name": "Heading", "selector": "h2"},
    {"name": "Link", "selector": "a", "format": "attribute", "attribute": "href"},
]


class TestExtractSection:
    def test_value(self):
        assert extract_section(DOC, {"name": "T", "selector": "h1"}) == {
            "name": "T",
            "type": "value",
            "value": "Title",
        }

    def test_value_not_found_is_empty(self):
        assert extract_section(DOC, {"name": "T", "selector": "h6"})["value"] == ""

    def test_empty_selector_selects_body(self):
        result = extract_section(DOC, {"name": "All", "selector": ""})
        assert result["value"].startswith("Title")
        assert "nothing useful" in result["value"]

    def test_fallback_selectors(self):
        assert extract_section(DOC, {"name": "T", "selector": "h6, h1"})["value"] == "Title"

    def test_list(self):
        result = extract_section(DOC, {"name": "L", "selector": "li", "multiple": True})
        assert result == {"name": "L", "type": "list", "items": ["a", "b", "c"]}

    def test_list_respects_max_items_and_limit(self):
        section = {"name": "L", "selector": "li", "multiple": True, "max_items": 2}
        assert extract_section(DOC, section)["items"] == ["a", "b"]
        assert extract_section(DOC, section, limit=1)["items"] == ["a"]

    def test_list_drops_empty_values(self):
        doc = soup("<li>a</li><li> </li><li>b</li>")
        assert extract_section(doc, {"name": "L", "selector": "li", "multiple": True})["items"] == [
            "a",
            "b",
        ]

    def test_group(self):
        section = {"name": "G", "selector": ".card", "children": CHILDREN}
        assert extract_section(DOC, section) == {
            "name": "G",
            "type": "group",
            "fields": {"Heading": "One", "Link": "/1"},
        }

    def test_group_without_match_has_no_fields(self):
        section = {"name": "G", "selector": ".nope", "children": CHILDREN}
        assert extract_section(DOC, section)["fields"] == {}

    def test_records_omit_empty_fields_and_drop_empty_records(self):
        section = {"name": "R", "selector": ".card", "multiple": True, "children": CHILDREN}
        assert extract_section(DOC, section) == {
            "name": "R",
            "type": "records",
            "items": [{"Heading": "One", "Link": "/1"}, {"Link": "/2"}],
        }

    def test_records_resolve_relative_urls_with_page_url(self):
        children = [{**CHILDREN[1], "transform": "resolve_href"}]
        section = {"name": "R", "selector": ".card", "multiple": True, "children": children}
        items = extract_section(DOC, section, "https://e.com/base/")["items"]
        assert items == [{"Link": "https://e.com/1"}, {"Link": "https://e.com/2"}]

    def test_records_limit_applies_before_dropping_empty_records(self):
        section = {"name": "R", "selector": ".card", "multiple": True, "children": CHILDREN}
        assert extract_section(DOC, section, limit=1)["items"] == [{"Heading": "One", "Link": "/1"}]

    def test_required_section_missing(self):
        with pytest.raises(ValueError, match="Required section 'T' not found"):
            extract_section(DOC, {"name": "T", "selector": "h6", "required": True})

    def test_required_field_missing(self):
        children = [{"name": "Price", "selector": ".price", "required": True}]
        section = {"name": "R", "selector": ".card", "multiple": True, "children": children}
        with pytest.raises(ValueError, match="Required field 'Price' not found in section 'R'"):
            extract_section(DOC, section)

    def test_optional_missing_field_is_fine(self):
        children = [{"name": "Price", "selector": ".price"}]
        section = {"name": "R", "selector": ".card", "multiple": True, "children": children}
        assert extract_section(DOC, section)["items"] == []


class TestExtractTemplate:
    HTML = (
        "<body><nav>menu</nav><h1>Hi</h1>"
        + "".join(f"<div class='r'><h3>R{i}</h3></div>" for i in range(1, 5))
        + "<ul><li>x</li><li>y</li><li>z</li></ul></body>"
    )
    SECTIONS = (
        {"name": "Title", "selector": "h1"},
        {
            "name": "First",
            "selector": ".r",
            "multiple": True,
            "max_items": 3,
            "children": [{"name": "T", "selector": "h3"}],
        },
        {
            "name": "Second",
            "selector": ".r",
            "multiple": True,
            "max_items": 3,
            "children": [{"name": "T", "selector": "h3"}],
        },
        {"name": "List", "selector": "li", "multiple": True, "max_items": 3},
    )

    def run(self, template, **kwargs):
        return extract_template(soup(self.HTML), template, **kwargs)

    def test_returns_sections_in_template_order(self):
        results = self.run({"sections": list(self.SECTIONS)})
        assert [(r["name"], r["type"]) for r in results] == [
            ("Title", "value"),
            ("First", "records"),
            ("Second", "records"),
            ("List", "list"),
        ]
        assert [len(r["items"]) for r in results[1:]] == [3, 3, 3]

    def test_max_results_overrides_only_the_first_records_section(self):
        results = self.run({"sections": list(self.SECTIONS)}, max_results=1)
        assert [len(r["items"]) for r in results[1:]] == [1, 3, 3]
        results = self.run({"sections": list(self.SECTIONS)}, max_results=4)
        assert [len(r["items"]) for r in results[1:]] == [4, 3, 3]

    def test_default_remove_applies_without_a_remove_list(self):
        results = self.run({"sections": [{"name": "All", "selector": "body"}]})
        assert "menu" not in results[0]["value"]

    def test_explicit_remove_list_replaces_defaults(self):
        results = self.run({"remove": [], "sections": [{"name": "All", "selector": "body"}]})
        assert "menu" in results[0]["value"]

    def test_page_url_is_passed_to_transforms(self):
        template = {
            "sections": [
                {
                    "name": "Link",
                    "selector": "a",
                    "format": "attribute",
                    "attribute": "href",
                    "transform": "resolve_href",
                }
            ]
        }
        [result] = extract_template(soup("<a href='/p'>x</a>"), template, "https://e.com/a")
        assert result["value"] == "https://e.com/p"


class TestBuiltinTemplateFixtures:
    def test_fixtures_cover_every_builtin_template_once(self):
        covered = [case["template"] for case in TEMPLATE_CASES]
        assert len(covered) == len(set(covered))
        assert set(covered) == set(BUILTIN_TEMPLATES)

    @pytest.mark.parametrize("case", TEMPLATE_CASES, ids=lambda case: case["template"])
    def test_template_extracts_fixture_page(self, case):
        template = BUILTIN_TEMPLATES[case["template"]]
        page_url = case.get("url", "https://example.com/page")
        sections = extract_template(soup(case["html"]), template, page_url)
        output = compose_sections(sections)
        for expected in case["expect"]:
            assert expected in output, f"missing {expected!r} in {output!r}"
        for absent in case["absent"]:
            assert absent not in output, f"unexpected {absent!r} in {output!r}"
