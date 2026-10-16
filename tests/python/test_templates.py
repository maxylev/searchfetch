"""Template loading, validation, URL matching and URL resolution (``searchfetch.templates``)."""

import json
import re
import urllib.parse
from pathlib import Path

import pytest

from searchfetch.templates import (
    BUILTIN_TEMPLATES,
    ENGINE_ALIASES,
    TEMPLATES_DIR,
    build_search_url,
    detect_template,
    get_template,
    load_templates,
    map_search_params,
    parse_inline_template,
    resolve_page_template,
    resolve_search_candidates,
    resolve_url_template,
    validate_template,
    validate_url,
)

TEMPLATES = list(BUILTIN_TEMPLATES.values())
ORDER = {t["name"]: t["order"] for t in TEMPLATES}
CONTENT_TEMPLATES = [t for t in TEMPLATES if t.get("url_patterns") and not t.get("url_template")]


def matches(name: str, url: str) -> bool:
    return any(re.search(pattern, url) for pattern in BUILTIN_TEMPLATES[name]["url_patterns"])


def query(url: str) -> dict[str, list[str]]:
    return urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)


def section(name: str, template: str) -> dict:
    return next(s for s in BUILTIN_TEMPLATES[template]["sections"] if s["name"] == name)


# --- Built-in template files -------------------------------------------------------------------


class TestBuiltinTemplateFiles:
    def test_every_json_file_is_loaded_as_an_object(self):
        files = sorted(TEMPLATES_DIR.glob("*.json"))
        assert len(files) == len(TEMPLATES)
        assert all(isinstance(json.loads(f.read_text(encoding="utf-8")), dict) for f in files)

    def test_names_are_unique_non_empty_strings(self):
        names = [t["name"] for t in TEMPLATES]
        assert all(isinstance(n, str) and n for n in names)
        assert len(names) == len(set(names)) == len(BUILTIN_TEMPLATES)

    def test_every_template_has_a_numeric_unique_order(self):
        orders = [t["order"] for t in TEMPLATES]
        assert all(isinstance(o, (int, float)) and not isinstance(o, bool) for o in orders)
        assert len(orders) == len(set(orders))

    def test_templates_are_sorted_by_order(self):
        orders = [t["order"] for t in TEMPLATES]
        assert orders == sorted(orders)

    def test_every_template_passes_validation(self):
        for template in TEMPLATES:
            assert validate_template(template) is template

    def test_every_template_has_a_non_empty_remove_list_except_raw(self):
        for template in TEMPLATES:
            assert isinstance(template["remove"], list)
            assert bool(template["remove"]) is (template["name"] != "raw")

    def test_fallbacks_point_at_existing_search_templates(self):
        for template in TEMPLATES:
            if "fallback" in template:
                assert BUILTIN_TEMPLATES[template["fallback"]].get("url_template")

    def test_content_templates_do_not_match_search_engine_urls(self):
        for template in CONTENT_TEMPLATES:
            for pattern in template["url_patterns"]:
                assert not re.search(pattern, "https://www.google.com/search?q=test"), pattern
                assert not re.search(pattern, "https://duckduckgo.com/?q=test"), pattern

    def test_engine_aliases_resolve_to_search_templates(self):
        for alias, name in ENGINE_ALIASES.items():
            assert BUILTIN_TEMPLATES[name]["engine"] == alias


# --- URL pattern matching ----------------------------------------------------------------------

URL_CASES = {
    "docs-rs": (
        [
            "https://docs.rs/ladon/latest/ladon/",
            "https://docs.rs/ladon/latest/ladon/struct.KeyInfo.html",
            "https://docs.rs/solana/latest/solana/broadcast_stage/index.html",
            "https://docs.rs/tokio/latest/tokio/",
            "https://docs.rs/serde/latest/serde/enum.Result.html",
            "https://docs.rs/syn/2.0.0/syn/struct.ItemFn.html",
            "https://docs.rs/clap/latest/clap/",
            "https://docs.rs/axum/latest/axum/",
        ],
        [],
    ),
    "docker-hub": (
        [
            "https://hub.docker.com/_/postgres",
            "https://hub.docker.com/_/postgres/",
            "https://hub.docker.com/r/nginxinc/nginx-unprivileged",
            "https://hub.docker.com/r/nginxinc/nginx-unprivileged/",
            "https://hub.docker.com/_/node",
            "https://hub.docker.com/r/library/python/",
        ],
        [],
    ),
    "docs-page": (
        [
            "https://example.readthedocs.io/en/latest/",
            "https://docs.mintlify.com/introduction",
            "https://example.mintlify.dev/",
            "https://viem.sh/docs/clients/public",
            "https://docs.example.com/getting-started",
            "https://solana.com/docs/rpc/http/getslot",
            "https://docs.python.org/3/library/os.html",
            "https://nextjs.org/docs/app/building-your-application/routing",
            "https://docs.solidjs.com/concepts/intro-to-reactivity",
            "https://htmx.org/docs/",
            "https://docs.astro.build/en/guides/deploy/",
            "https://go.dev/doc/",
            "https://docs.deno.com/runtime/manual/",
            "https://svelte.dev/docs/introduction",
            "https://tailwindcss.com/docs/installation",
        ],
        [
            "https://fastapi.tiangolo.com/tutorial/first-steps/",
            "https://react.dev/reference/react/useState",
            "https://elixir-lang.org/getting-started/introduction.html",
        ],
    ),
    "crates-package": (
        ["https://crates.io/crates/serde", "https://crates.io/crates/tokio/1.0.0"],
        [],
    ),
    "npm-package": (
        ["https://www.npmjs.com/package/react", "https://npmjs.com/package/lodash"],
        [],
    ),
    "pypi-package": (
        ["https://pypi.org/project/requests/", "https://pypi.org/project/django/3.2/"],
        [],
    ),
    "github-repo": (
        ["https://github.com/maxylev/searchfetch", "https://github.com/wevm/viem/"],
        [],
    ),
    "github-issue": (
        [
            "https://github.com/maxylev/searchfetch/issues/1",
            "https://github.com/wevm/viem/pull/42",
        ],
        [],
    ),
    "wikipedia": (
        [
            "https://en.wikipedia.org/wiki/Rust_(programming_language)",
            "https://de.wikipedia.org/wiki/Rust_(Programmiersprache)",
            "https://fr.wikipedia.org/wiki/Python_(langage)",
            "https://ja.wikipedia.org/wiki/Linux",
        ],
        [
            "https://en.wikipedia.org/wiki/Special:Search?search=Rust",
            "https://en.wikipedia.org/wiki/Wikipedia:About",
            "https://en.wikipedia.org/wiki/File:Example.jpg",
            "https://en.wikipedia.org/wiki/Category:Programming_languages",
        ],
    ),
    "reddit": (
        [
            "https://www.reddit.com/r/rust/comments/1ujqvqx/rust_1961_is_out/",
            "https://old.reddit.com/r/python/comments/abc123/some_post/",
            "https://reddit.com/r/programming/comments/xyz789/interesting/",
        ],
        [],
    ),
    "mdn-web-docs": (
        [
            "https://developer.mozilla.org/en-US/docs/Web/JavaScript",
            "https://developer.mozilla.org/en-US/docs/Web/HTML/Element/div",
            "https://developer.mozilla.org/en-US/docs/Web/CSS/color",
        ],
        [],
    ),
    "gitlab": (
        [
            "https://gitlab.com/gitlab-org/gitlab",
            "https://gitlab.com/gitlab-org/gitlab/",
            "https://gitlab.com/gitlab-org/gitaly/-/tree/main/",
        ],
        [],
    ),
    "youtube": (
        [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtube.com/watch?v=abcdefghijk",
            "https://youtu.be/dQw4w9WgXcQ",
        ],
        [],
    ),
    "devto": (
        [
            "https://dev.to/lydiahallie/javascript-visualized-promises-async-await-5gke",
            "https://dev.to/someuser/some-article-123abc",
        ],
        [],
    ),
    "go-pkg": (
        [
            "https://pkg.go.dev/fmt",
            "https://pkg.go.dev/net/http",
            "https://pkg.go.dev/encoding/json/",
        ],
        ["https://pkg.go.dev/"],
    ),
    "javadoc": (
        [
            "https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/ArrayList.html",
            "https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/lang/String.html",
            "https://docs.oracle.com/javase/8/docs/api/java/util/Map.html",
        ],
        [],
    ),
}

POSITIVE = [(name, url) for name, (urls, _) in URL_CASES.items() for url in urls]
NEGATIVE = [(name, url) for name, (_, urls) in URL_CASES.items() for url in urls]


class TestUrlPatternMatching:
    def test_every_content_template_has_url_cases(self):
        assert set(URL_CASES) == {t["name"] for t in CONTENT_TEMPLATES}

    @pytest.mark.parametrize(("name", "url"), POSITIVE)
    def test_pattern_matches(self, name, url):
        assert matches(name, url), f"{url} should match {name}"

    @pytest.mark.parametrize(("name", "url"), NEGATIVE)
    def test_pattern_does_not_match(self, name, url):
        assert not matches(name, url), f"{url} should not match {name}"

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://docs.rs/solana/latest/solana/", "docs-rs"),
            ("https://en.wikipedia.org/wiki/Python_(programming_language)", "wikipedia"),
            ("https://pkg.go.dev/fmt", "go-pkg"),
            (
                "https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/ArrayList.html",
                "javadoc",
            ),
            ("https://github.com/maxylev/searchfetch", "github-repo"),
            ("https://crates.io/crates/serde", "crates-package"),
            ("https://example.com/docs/intro", "docs-page"),
        ],
    )
    def test_detect_template_picks_the_most_specific_by_order(self, url, expected):
        assert detect_template(url)["name"] == expected

    def test_detect_template_returns_none_for_unknown_urls(self):
        assert detect_template("https://example.com/random-page") is None

    def test_detect_template_respects_order_in_a_custom_set(self):
        templates = {
            "late": {"name": "late", "order": 2, "url_patterns": ["example"]},
            "early": {"name": "early", "order": 1, "url_patterns": ["example"]},
        }
        loaded = dict(sorted(templates.items(), key=lambda kv: kv[1]["order"]))
        assert detect_template("https://example.com", loaded)["name"] == "early"


class TestOrderingInvariants:
    def test_github_templates_come_before_every_other_content_template(self):
        github = ("github-repo", "github-issue")
        for template in CONTENT_TEMPLATES:
            if template["name"] not in github:
                assert all(ORDER[g] < template["order"] for g in github), template["name"]

    @pytest.mark.parametrize(
        ("first", "second"),
        [
            ("crates-package", "docs-rs"),
            ("docs-rs", "docs-page"),
            ("javadoc", "docs-page"),
            ("wikipedia", "mdn-web-docs"),
            ("github-issue", "gitlab"),
            ("npm-package", "duckduckgo-search"),
            ("pypi-package", "duckduckgo-search"),
            ("crates-package", "google-search"),
            ("duckduckgo-search", "duckduckgo-lite-search"),
        ],
    )
    def test_order(self, first, second):
        assert ORDER[first] < ORDER[second]

    def test_raw_is_only_used_by_explicit_name(self):
        assert ORDER["raw"] > 100
        assert ORDER["raw"] == max(ORDER.values())


# --- Per-template structure --------------------------------------------------------------------

SECTIONS = {
    "docs-rs": {"Crate": {}, "Title": {}, "Content": {"format": "markdown"}},
    "docker-hub": {
        "Image": {},
        "Description": {"format": "attribute", "attribute": "content"},
        "Content": {},
    },
    "wikipedia": {
        "Title": {"required": True},
        "Content": {"format": "markdown", "required": True},
    },
    "reddit": {
        "Title": {"required": True},
        "Author": {},
        "Content": {},
        "Subreddit": {},
        "Link": {"format": "attribute", "attribute": "href"},
    },
    "mdn-web-docs": {"Title": {}, "Content": {"format": "markdown"}},
    "gitlab": {
        "Title": {},
        "Description": {"format": "attribute", "attribute": "content"},
        "README": {},
    },
    "youtube": {
        "Title": {"format": "attribute", "attribute": "content", "required": True},
        "Channel": {},
        "Description": {},
    },
    "devto": {"Title": {}, "Author": {}, "Content": {"format": "markdown", "required": True}},
    "go-pkg": {"Title": {}, "Overview": {}, "Documentation": {}},
    "javadoc": {"Title": {}, "Content": {"format": "markdown"}},
    "raw": {"Content": {"selector": "body", "format": "markdown", "required": False}},
}


class TestSectionStructure:
    @pytest.mark.parametrize(("template", "sections"), SECTIONS.items(), ids=list(SECTIONS))
    def test_sections_have_expected_properties(self, template, sections):
        names = {s["name"] for s in BUILTIN_TEMPLATES[template]["sections"]}
        assert set(sections) <= names
        for name, expected in sections.items():
            actual = section(name, template)
            for key, value in expected.items():
                assert actual[key] == value, f"{template}/{name}/{key}"

    def test_docs_page_has_a_markdown_source_url(self):
        assert BUILTIN_TEMPLATES["docs-page"]["source_url"] == "{url}.md"

    @pytest.mark.parametrize("name", ["docs-rs", "docker-hub"])
    def test_sites_without_raw_markdown_have_no_source_url(self, name):
        assert "source_url" not in BUILTIN_TEMPLATES[name]

    def test_docs_rs_removes_sidebar_and_footer(self):
        remove = BUILTIN_TEMPLATES["docs-rs"]["remove"]
        assert "nav.sidebar" in remove or "nav" in remove
        assert "footer" in remove

    @pytest.mark.parametrize("name", ["wikipedia", "devto"])
    def test_h1_lives_inside_header_so_header_is_kept(self, name):
        assert "header" not in BUILTIN_TEMPLATES[name]["remove"]

    def test_raw_removes_nothing(self):
        assert BUILTIN_TEMPLATES["raw"]["remove"] == []

    def test_search_templates_declare_engine_and_results(self):
        for name in ("duckduckgo-search", "duckduckgo-lite-search", "google-search"):
            template = BUILTIN_TEMPLATES[name]
            assert template["engine"] in ENGINE_ALIASES
            results = template["sections"][0]
            assert results["multiple"] and results["children"]
            assert {c["name"] for c in results["children"]} >= {"Title", "URL"}


# --- Validation --------------------------------------------------------------------------------


def template_with(**section_fields):
    return {"name": "t", "sections": [{"name": "S", **section_fields}]}


class TestValidation:
    @pytest.mark.parametrize(
        ("template", "message"),
        [
            ([], "JSON object"),
            ({"sections": []}, 'non-empty "name"'),
            ({"name": "", "sections": []}, 'non-empty "name"'),
            ({"name": 5, "sections": []}, 'non-empty "name"'),
            ({"name": "t"}, '"sections" list'),
            ({"name": "t", "sections": {}}, '"sections" list'),
            ({"name": "t", "sections": [{}]}, 'non-empty "name"'),
            ({"name": "t", "sections": ["x"]}, 'non-empty "name"'),
            (template_with(selector=3), '"selector" must be a string'),
            (template_with(format="xml"), "unknown format 'xml'"),
            (template_with(format="attribute"), 'requires an "attribute"'),
            (template_with(format="attribute", attribute=""), 'requires an "attribute"'),
            (template_with(transform="nope"), "unknown transform 'nope'"),
            (template_with(transform=["strip", "nope"]), "unknown transform 'nope'"),
            (template_with(max_items=0), "positive integer"),
            (template_with(max_items=-1), "positive integer"),
            (template_with(max_items=1.5), "positive integer"),
            (template_with(max_items="3"), "positive integer"),
            (template_with(max_items=True), "positive integer"),
            (template_with(children="x"), '"children" must be a list'),
            (template_with(children=[{"selector": "a"}]), 'non-empty "name"'),
            (
                template_with(children=[{"name": "C", "children": [{"name": "D"}]}]),
                "cannot be nested",
            ),
            ({"name": "t", "sections": [], "url_patterns": "x"}, "list of strings"),
            ({"name": "t", "sections": [], "url_patterns": [1]}, "list of strings"),
            ({"name": "t", "sections": [], "url_patterns": ["("]}, "invalid URL pattern"),
        ],
    )
    def test_invalid_templates_are_rejected(self, template, message):
        with pytest.raises(ValueError, match=message):
            validate_template(template)

    def test_error_names_the_offending_section(self):
        with pytest.raises(ValueError, match="section 'S'"):
            validate_template(template_with(format="xml"), "Template file 'x.json'")

    @pytest.mark.parametrize(
        "fields",
        [
            {},
            {"format": "html"},
            {"format": "attribute", "attribute": "href"},
            {"transform": ["strip", "resolve_href"], "max_items": 3, "multiple": True},
            {"children": [{"name": "C", "selector": "a"}]},
        ],
    )
    def test_valid_sections_are_accepted(self, fields):
        template = template_with(**fields)
        assert validate_template(template) is template


class TestParseInlineTemplate:
    def test_defaults_name_to_custom(self):
        template = parse_inline_template('{"sections": []}')
        assert template["name"] == "custom"

    def test_keeps_given_name(self):
        assert parse_inline_template('{"name": "mine", "sections": []}')["name"] == "mine"

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("{not valid json", "Invalid inline JSON"),
            ('{"name": "broken"}', '"sections" list'),
            ('{"sections": [{"name": "S", "format": "xml"}]}', "Inline template"),
            ("[]", "JSON object"),
        ],
    )
    def test_errors(self, text, message):
        with pytest.raises(ValueError, match=message):
            parse_inline_template(text)


class TestLoadTemplates:
    def write(self, directory: Path, name: str, data) -> None:
        text = data if isinstance(data, str) else json.dumps(data)
        (directory / name).write_text(text, encoding="utf-8")

    def test_sorts_by_order_and_puts_unordered_last(self, tmp_path):
        self.write(tmp_path, "a.json", {"name": "a", "sections": []})
        self.write(tmp_path, "b.json", {"name": "b", "order": 2, "sections": []})
        self.write(tmp_path, "c.json", {"name": "c", "order": 1, "sections": []})
        assert list(load_templates(tmp_path)) == ["c", "b", "a"]

    def test_empty_directory(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_templates(tmp_path)

    def test_invalid_json_names_the_file(self, tmp_path):
        self.write(tmp_path, "bad.json", "{")
        with pytest.raises(ValueError, match=r"Invalid JSON in template file '.*bad\.json'"):
            load_templates(tmp_path)

    def test_invalid_template_names_the_file(self, tmp_path):
        self.write(tmp_path, "bad.json", {"name": "bad"})
        with pytest.raises(ValueError, match=r"bad\.json"):
            load_templates(tmp_path)

    def test_duplicate_names(self, tmp_path):
        self.write(tmp_path, "a.json", {"name": "same", "sections": []})
        self.write(tmp_path, "b.json", {"name": "same", "sections": []})
        with pytest.raises(ValueError, match="Duplicate template 'same'"):
            load_templates(tmp_path)


class TestLookup:
    def test_get_template(self):
        assert get_template("raw")["name"] == "raw"

    def test_unknown_template_lists_available_names(self):
        with pytest.raises(ValueError, match=r"Unknown template 'nope'\. Available: .*raw"):
            get_template("nope")

    def test_resolve_page_template_auto(self):
        assert (
            resolve_page_template("https://docs.rs/tokio/latest/tokio/", "auto")["name"]
            == "docs-rs"
        )
        assert resolve_page_template("https://example.com/x", "auto") is None

    def test_resolve_page_template_by_name_ignores_url(self):
        template = resolve_page_template("https://example.com/some-page", "npm-package")
        assert template["name"] == "npm-package"

    def test_resolve_page_template_inline(self):
        template = resolve_page_template("https://example.com", '  {"name": "c", "sections": []}')
        assert template["name"] == "c"

    @pytest.mark.parametrize(
        ("value", "message"),
        [("nonexistent", "Unknown template"), ("{not valid json", "Invalid inline JSON")],
    )
    def test_resolve_page_template_errors(self, value, message):
        with pytest.raises(ValueError, match=message):
            resolve_page_template("https://example.com", value)


class TestValidateUrl:
    @pytest.mark.parametrize("url", ["https://example.com", "http://localhost:8080/a?b=1"])
    def test_accepts_http_urls(self, url):
        assert validate_url(url) == url

    @pytest.mark.parametrize(
        "url",
        [
            "file:///tmp/a",
            "ftp://example.com",
            "javascript:alert(1)",
            "https://",
            "example.com",
            "",
        ],
    )
    def test_rejects_other_urls(self, url):
        with pytest.raises(ValueError, match="HTTP or HTTPS"):
            validate_url(url)


# --- URL templates and search parameters -------------------------------------------------------


class TestResolveUrlTemplate:
    def template(self, **params):
        return {
            "name": "test",
            "url_template": "https://example.com/?q={query}",
            "url_params": params,
        }

    def test_uses_given_values_then_defaults(self):
        t = {
            "name": "t",
            "url_template": "https://example.com/{path}/{page}",
            "url_params": {"path": {"default": "home"}, "page": {"default": "index"}},
        }
        assert resolve_url_template(t, {"path": "about"}) == "https://example.com/about/index"

    def test_missing_required_parameter(self):
        with pytest.raises(ValueError, match="Required URL parameter 'query'"):
            resolve_url_template(self.template(query={"required": True}), {})

    def test_missing_optional_parameter_is_empty(self):
        assert resolve_url_template(self.template(), {}) == "https://example.com/?q="

    def test_template_without_url_template(self):
        with pytest.raises(ValueError, match="has no url_template"):
            resolve_url_template({"name": "t"}, {})

    def test_substituted_values_are_not_expanded_again(self):
        t = {"url_template": "https://example.com/{query}/{x}/{x}/{missing}"}
        assert (
            resolve_url_template(t, {"query": "{x}", "x": "$&"}) == "https://example.com/{x}/$&/$&/"
        )

    @pytest.mark.parametrize(
        ("value", "encoded"),
        [
            ("hello world", "hello%20world"),
            ("a&b=c/d?e#f", "a%26b%3Dc%2Fd%3Fe%23f"),
            ("-_.!~*'()", "-_.!~*'()"),
            ("é😀", "%C3%A9%F0%9F%98%80"),
            ("a+b", "a%2Bb"),
        ],
    )
    def test_url_encoding_matches_javascript_encode_uri_component(self, value, encoded):
        template = self.template(query={"encode": "url"})
        assert (
            resolve_url_template(template, {"query": value}) == f"https://example.com/?q={encoded}"
        )

    def test_values_without_encode_are_inserted_verbatim(self):
        assert (
            resolve_url_template(self.template(), {"query": "a b"}) == "https://example.com/?q=a b"
        )


class TestSearchParams:
    def test_duckduckgo(self):
        assert map_search_params("duckduckgo", "q", "us-en", True) == {
            "query": "q",
            "kl": "us-en",
            "kp": "1",
        }
        assert map_search_params("duckduckgo", "q", None, False) == {"query": "q", "kp": "-2"}
        assert map_search_params("duckduckgo", "q", None, None) == {"query": "q"}

    def test_google_region_is_split_into_country_and_language(self):
        assert map_search_params("google", "q", "us-en", None) == {
            "query": "q",
            "gl": "us",
            "hl": "en",
        }
        assert map_search_params("google", "q", "de", None) == {"query": "q", "gl": "de"}

    def test_google_ignores_safe_search_here(self):
        assert map_search_params("google", "q", None, True) == {"query": "q"}

    def test_unknown_engine_only_gets_the_query(self):
        assert map_search_params(None, "q", "us-en", True) == {"query": "q"}


class TestBuildSearchUrl:
    def google(self, region=None, safe=None):
        return build_search_url(BUILTIN_TEMPLATES["google-search"], "a b", region, safe)

    def test_google_region_and_safe_search_on(self):
        assert query(self.google("us-en", True)) == {
            "udm": ["14"],
            "q": ["a b"],
            "hl": ["en"],
            "gl": ["us"],
            "safe": ["active"],
        }

    def test_google_safe_search_off(self):
        assert query(self.google(safe=False))["safe"] == ["off"]

    def test_google_default_has_no_safe_parameter(self):
        assert "safe" not in query(self.google())

    def test_safe_is_appended_with_question_mark_when_url_has_no_query(self):
        template = {"name": "g", "engine": "google", "url_template": "https://g.example/search"}
        assert build_search_url(template, "q", None, True) == "https://g.example/search?safe=active"

    def test_duckduckgo_parameters(self):
        url = build_search_url(BUILTIN_TEMPLATES["duckduckgo-search"], "a&b", "de-de", False)
        assert url == "https://html.duckduckgo.com/html/?q=a%26b&kl=de-de&kp=-2"

    def test_duckduckgo_defaults(self):
        url = build_search_url(BUILTIN_TEMPLATES["duckduckgo-lite-search"], "x", None, None)
        assert url == "https://lite.duckduckgo.com/lite/?q=x&kl=wt-wt&kp=-1"

    def test_non_search_template(self):
        with pytest.raises(ValueError, match="not a search template"):
            build_search_url(BUILTIN_TEMPLATES["raw"], "q", None, None)


class TestResolveSearchCandidates:
    @staticmethod
    def names(engine):
        return [t["name"] for t, _ in resolve_search_candidates(engine, "test", None, None)]

    @pytest.mark.parametrize(
        ("engine", "expected"),
        [
            ("google", ["google-search", "duckduckgo-search", "duckduckgo-lite-search"]),
            ("google-search", ["google-search", "duckduckgo-search", "duckduckgo-lite-search"]),
            ("duckduckgo", ["duckduckgo-search", "duckduckgo-lite-search"]),
            ("duckduckgo-lite-search", ["duckduckgo-lite-search"]),
        ],
    )
    def test_fallback_chains(self, engine, expected):
        assert self.names(engine) == expected

    def test_each_candidate_has_its_own_url(self):
        urls = [u for _, u in resolve_search_candidates("google", "test", "us-en", None)]
        assert urls[0].startswith("https://www.google.com/search?")
        assert urls[1].startswith("https://html.duckduckgo.com/html/?q=test&kl=us-en")
        assert urls[2].startswith("https://lite.duckduckgo.com/lite/?q=test")

    def test_region_and_safe_search_apply_to_every_candidate(self):
        candidates = resolve_search_candidates("google", "test", "us-en", True)
        assert "gl=us" in candidates[0][1] and "safe=active" in candidates[0][1]
        assert "kl=us-en" in candidates[1][1] and "kp=1" in candidates[1][1]

    def test_inline_template_has_no_fallback(self):
        inline = json.dumps(
            {"name": "mine", "url_template": "https://s.example/?q={query}", "sections": []}
        )
        [(template, url)] = resolve_search_candidates(inline, "a b", None, None)
        assert template["name"] == "mine"
        assert url == "https://s.example/?q=a b"

    def test_fallback_cycles_terminate(self):
        inline = json.dumps(
            {
                "name": "duckduckgo-lite-search",
                "url_template": "https://s.example/?q={query}",
                "fallback": "duckduckgo-lite-search",
                "sections": [],
            }
        )
        assert len(resolve_search_candidates(inline, "q", None, None)) == 1

    def test_unknown_engine(self):
        with pytest.raises(ValueError, match="Unknown template 'bing'"):
            resolve_search_candidates("bing", "q", None, None)

    def test_page_template_is_not_a_search_engine(self):
        with pytest.raises(ValueError, match="not a search template"):
            resolve_search_candidates("raw", "q", None, None)
