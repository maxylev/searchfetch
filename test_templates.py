"""Tests for searchfetch templates: JSON validity, URL matching, extraction.

Run with: uv run --extra dev pytest test_templates.py -v
"""

import json
import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers: lightweight reimplementations of template-loading internals so we
# can test without spawning a browser or importing the MCP server.
# ---------------------------------------------------------------------------

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def load_all_templates():
    """Load every *.json template from the templates/ directory."""
    json_files = sorted(TEMPLATES_DIR.glob("*.json"))
    if not json_files:
        pytest.fail(f"No template JSON files found in '{TEMPLATES_DIR}'")

    templates = []
    for filepath in json_files:
        data = json.loads(filepath.read_text(encoding="utf-8"))
        name = data.get("name")
        if not name or not isinstance(name, str):
            pytest.fail(f"Template '{filepath}' is missing a valid 'name' field")
        templates.append(data)

    templates.sort(key=lambda t: t.get("order", 999))
    return templates


ALL_TEMPLATES = load_all_templates()
TEMPLATES_BY_NAME = {t["name"]: t for t in ALL_TEMPLATES}


# ---------------------------------------------------------------------------
# 1. JSON validity & required fields
# ---------------------------------------------------------------------------


class TestTemplateJsonValidity:
    def test_all_files_are_valid_json(self):
        json_files = sorted(TEMPLATES_DIR.glob("*.json"))
        assert len(json_files) >= 18  # originals + new templates
        for fp in json_files:
            data = json.loads(fp.read_text(encoding="utf-8"))
            assert isinstance(data, dict), f"{fp.name}: not a JSON object"

    def test_every_template_has_name(self):
        for t in ALL_TEMPLATES:
            assert "name" in t, f"Template {t} missing 'name'"
            assert isinstance(t["name"], str) and t["name"], (
                f"Template 'name' must be a non-empty string, got {t['name']!r}"
            )

    def test_every_template_has_order(self):
        for t in ALL_TEMPLATES:
            assert "order" in t, f"Template '{t['name']}' missing 'order'"
            assert isinstance(t["order"], (int, float)), (
                f"Template '{t['name']}' order must be a number"
            )

    def test_no_duplicate_names(self):
        names = [t["name"] for t in ALL_TEMPLATES]
        assert len(names) == len(set(names)), f"Duplicate template names: {names}"

    def test_no_duplicate_orders(self):
        orders = [t["order"] for t in ALL_TEMPLATES]
        duplicates = [o for o in orders if orders.count(o) > 1]
        assert not set(duplicates), (
            f"Duplicate order values (templates with same order): {set(duplicates)}"
        )

    def test_sections_is_list(self):
        for t in ALL_TEMPLATES:
            if "sections" in t:
                assert isinstance(t["sections"], list), (
                    f"Template '{t['name']}': sections must be a list"
                )


# ---------------------------------------------------------------------------
# 2. URL pattern matching
# ---------------------------------------------------------------------------


DOCS_RS_URLS = [
    "https://docs.rs/ladon/latest/ladon/",
    "https://docs.rs/ladon/latest/ladon/struct.KeyInfo.html",
    "https://docs.rs/solana/latest/solana/",
    "https://docs.rs/solana/latest/solana/broadcast_stage/index.html",
    "https://docs.rs/tokio/latest/tokio/",
    "https://docs.rs/serde/latest/serde/enum.Result.html",
    "https://docs.rs/syn/2.0.0/syn/struct.ItemFn.html",
    "https://docs.rs/clap/latest/clap/",
    "https://docs.rs/reqwest/latest/reqwest/",
    "https://docs.rs/axum/latest/axum/",
]

DOCKER_HUB_URLS = [
    "https://hub.docker.com/_/postgres",
    "https://hub.docker.com/_/postgres/",
    "https://hub.docker.com/r/nginxinc/nginx-unprivileged",
    "https://hub.docker.com/r/nginxinc/nginx-unprivileged/",
    "https://hub.docker.com/_/node",
    "https://hub.docker.com/r/library/python/",
]

DOCS_PAGE_URLS = [
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
]

DOCS_PAGE_NONMATCH_URLS = [
    "https://fastapi.tiangolo.com/tutorial/first-steps/",
    "https://react.dev/reference/react/useState",
    "https://elixir-lang.org/getting-started/introduction.html",
]

CRATES_IO_URLS = [
    "https://crates.io/crates/serde",
    "https://crates.io/crates/tokio/1.0.0",
]

NPM_URLS = [
    "https://www.npmjs.com/package/react",
    "https://npmjs.com/package/lodash",
]

PYPI_URLS = [
    "https://pypi.org/project/requests/",
    "https://pypi.org/project/django/3.2/",
]

GITHUB_REPO_URLS = [
    "https://github.com/maxylev/searchfetch",
    "https://github.com/wevm/viem/",
]

GITHUB_ISSUE_URLS = [
    "https://github.com/maxylev/searchfetch/issues/1",
    "https://github.com/wevm/viem/pull/42",
]

WIKIPEDIA_URLS = [
    "https://en.wikipedia.org/wiki/Rust_(programming_language)",
    "https://de.wikipedia.org/wiki/Rust_(Programmiersprache)",
    "https://fr.wikipedia.org/wiki/Python_(langage)",
    "https://ja.wikipedia.org/wiki/Linux",
]

WIKIPEDIA_NONMATCH_URLS = [
    "https://en.wikipedia.org/wiki/Special:Search?search=Rust",
    "https://en.wikipedia.org/wiki/Wikipedia:About",
    "https://en.wikipedia.org/wiki/File:Example.jpg",
    "https://en.wikipedia.org/wiki/Category:Programming_languages",
]

REDDIT_URLS = [
    "https://www.reddit.com/r/rust/comments/1ujqvqx/rust_1961_is_out/",
    "https://old.reddit.com/r/python/comments/abc123/some_post/",
    "https://reddit.com/r/programming/comments/xyz789/interesting/",
]

MDN_URLS = [
    "https://developer.mozilla.org/en-US/docs/Web/JavaScript",
    "https://developer.mozilla.org/en-US/docs/Web/HTML/Element/div",
    "https://developer.mozilla.org/en-US/docs/Web/CSS/color",
]

GITLAB_URLS = [
    "https://gitlab.com/gitlab-org/gitlab",
    "https://gitlab.com/gitlab-org/gitlab/",
    "https://gitlab.com/gitlab-org/gitaly/-/tree/main/",
]

YOUTUBE_URLS = [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtube.com/watch?v=abcdefghijk",
    "https://youtu.be/dQw4w9WgXcQ",
]

DEVTO_URLS = [
    "https://dev.to/lydiahallie/javascript-visualized-promises-async-await-5gke",
    "https://dev.to/someuser/some-article-123abc",
]

GO_PKG_URLS = [
    "https://pkg.go.dev/fmt",
    "https://pkg.go.dev/net/http",
    "https://pkg.go.dev/encoding/json/",
]

GO_PKG_NONMATCH_URLS = [
    "https://pkg.go.dev/",  # root — not a package
]

JAVADOC_URLS = [
    "https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/ArrayList.html",
    "https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/lang/String.html",
    "https://docs.oracle.com/javase/8/docs/api/java/util/Map.html",
]


class TestUrlPatternMatching:
    @pytest.mark.parametrize("url", DOCS_RS_URLS)
    def test_docs_rs_matches_expected_urls(self, url):
        t = TEMPLATES_BY_NAME["docs-rs"]
        assert any(re.search(pat, url) for pat in t["url_patterns"]), (
            f"URL '{url}' should match docs-rs patterns: {t['url_patterns']}"
        )

    @pytest.mark.parametrize("url", DOCKER_HUB_URLS)
    def test_docker_hub_matches_expected_urls(self, url):
        t = TEMPLATES_BY_NAME["docker-hub"]
        assert any(re.search(pat, url) for pat in t["url_patterns"]), (
            f"URL '{url}' should match docker-hub patterns: {t['url_patterns']}"
        )

    @pytest.mark.parametrize("url", DOCS_PAGE_URLS)
    def test_docs_page_matches_expected_urls(self, url):
        t = TEMPLATES_BY_NAME["docs-page"]
        assert any(re.search(pat, url) for pat in t["url_patterns"]), (
            f"URL '{url}' should match docs-page patterns: {t['url_patterns']}"
        )

    @pytest.mark.parametrize("url", DOCS_PAGE_NONMATCH_URLS)
    def test_docs_page_does_not_match_non_docs_urls(self, url):
        """These are docs-adjacent URLs but don't use /docs/ path or docs. subdomain.
        They correctly fall through to generic fallback."""
        t = TEMPLATES_BY_NAME["docs-page"]
        assert not any(re.search(pat, url) for pat in t["url_patterns"]), (
            f"URL '{url}' should NOT match docs-page (no /docs/ or docs. pattern)"
        )

    @pytest.mark.parametrize("url", CRATES_IO_URLS)
    def test_crates_matches_expected_urls(self, url):
        t = TEMPLATES_BY_NAME["crates-package"]
        assert any(re.search(pat, url) for pat in t["url_patterns"])

    @pytest.mark.parametrize("url", NPM_URLS)
    def test_npm_matches_expected_urls(self, url):
        t = TEMPLATES_BY_NAME["npm-package"]
        assert any(re.search(pat, url) for pat in t["url_patterns"])

    @pytest.mark.parametrize("url", PYPI_URLS)
    def test_pypi_matches_expected_urls(self, url):
        t = TEMPLATES_BY_NAME["pypi-package"]
        assert any(re.search(pat, url) for pat in t["url_patterns"])

    @pytest.mark.parametrize("url", GITHUB_REPO_URLS)
    def test_github_repo_matches_expected_urls(self, url):
        t = TEMPLATES_BY_NAME["github-repo"]
        assert any(re.search(pat, url) for pat in t["url_patterns"])

    @pytest.mark.parametrize("url", GITHUB_ISSUE_URLS)
    def test_github_issue_matches_expected_urls(self, url):
        t = TEMPLATES_BY_NAME["github-issue"]
        assert any(re.search(pat, url) for pat in t["url_patterns"])

    @pytest.mark.parametrize("url", WIKIPEDIA_URLS)
    def test_wikipedia_matches_expected_urls(self, url):
        t = TEMPLATES_BY_NAME["wikipedia"]
        assert any(re.search(pat, url) for pat in t["url_patterns"]), (
            f"URL '{url}' should match wikipedia patterns: {t['url_patterns']}"
        )

    @pytest.mark.parametrize("url", WIKIPEDIA_NONMATCH_URLS)
    def test_wikipedia_does_not_match_special_pages(self, url):
        t = TEMPLATES_BY_NAME["wikipedia"]
        assert not any(re.search(pat, url) for pat in t["url_patterns"]), (
            f"URL '{url}' should NOT match wikipedia (not an article)"
        )

    @pytest.mark.parametrize("url", REDDIT_URLS)
    def test_reddit_matches_expected_urls(self, url):
        t = TEMPLATES_BY_NAME["reddit"]
        assert any(re.search(pat, url) for pat in t["url_patterns"]), (
            f"URL '{url}' should match reddit patterns: {t['url_patterns']}"
        )

    @pytest.mark.parametrize("url", MDN_URLS)
    def test_mdn_matches_expected_urls(self, url):
        t = TEMPLATES_BY_NAME["mdn-web-docs"]
        assert any(re.search(pat, url) for pat in t["url_patterns"]), (
            f"URL '{url}' should match mdn-web-docs patterns: {t['url_patterns']}"
        )

    @pytest.mark.parametrize("url", GITLAB_URLS)
    def test_gitlab_matches_expected_urls(self, url):
        t = TEMPLATES_BY_NAME["gitlab"]
        assert any(re.search(pat, url) for pat in t["url_patterns"]), (
            f"URL '{url}' should match gitlab patterns: {t['url_patterns']}"
        )

    @pytest.mark.parametrize("url", YOUTUBE_URLS)
    def test_youtube_matches_expected_urls(self, url):
        t = TEMPLATES_BY_NAME["youtube"]
        assert any(re.search(pat, url) for pat in t["url_patterns"]), (
            f"URL '{url}' should match youtube patterns: {t['url_patterns']}"
        )

    @pytest.mark.parametrize("url", DEVTO_URLS)
    def test_devto_matches_expected_urls(self, url):
        t = TEMPLATES_BY_NAME["devto"]
        assert any(re.search(pat, url) for pat in t["url_patterns"]), (
            f"URL '{url}' should match devto patterns: {t['url_patterns']}"
        )

    @pytest.mark.parametrize("url", GO_PKG_URLS)
    def test_go_pkg_matches_expected_urls(self, url):
        t = TEMPLATES_BY_NAME["go-pkg"]
        assert any(re.search(pat, url) for pat in t["url_patterns"]), (
            f"URL '{url}' should match go-pkg patterns: {t['url_patterns']}"
        )

    @pytest.mark.parametrize("url", GO_PKG_NONMATCH_URLS)
    def test_go_pkg_does_not_match_root(self, url):
        t = TEMPLATES_BY_NAME["go-pkg"]
        assert not any(re.search(pat, url) for pat in t["url_patterns"]), (
            f"URL '{url}' should NOT match go-pkg (root, not a package)"
        )

    @pytest.mark.parametrize("url", JAVADOC_URLS)
    def test_javadoc_matches_expected_urls(self, url):
        t = TEMPLATES_BY_NAME["javadoc"]
        assert any(re.search(pat, url) for pat in t["url_patterns"]), (
            f"URL '{url}' should match javadoc patterns: {t['url_patterns']}"
        )


# ---------------------------------------------------------------------------
# 3. Template ordering (docs-rs before docs-page, etc.)
# ---------------------------------------------------------------------------


class TestTemplateOrdering:
    def test_github_before_others(self):
        """GitHub templates should have lowest orders (matched first)."""
        gh_repo = TEMPLATES_BY_NAME["github-repo"]
        gh_issue = TEMPLATES_BY_NAME["github-issue"]
        for name, t in TEMPLATES_BY_NAME.items():
            if name in ("github-repo", "github-issue"):
                continue
            if "url_patterns" not in t:
                continue
            assert gh_repo["order"] < t["order"], (
                f"github-repo (order={gh_repo['order']}) should precede "
                f"'{name}' (order={t['order']})"
            )
            assert gh_issue["order"] < t["order"], (
                f"github-issue (order={gh_issue['order']}) should precede "
                f"'{name}' (order={t['order']})"
            )

    def test_crates_before_docs_rs(self):
        """crates.io is a specific site, docs.rs is broader — match crates first."""
        crates = TEMPLATES_BY_NAME["crates-package"]
        docs_rs = TEMPLATES_BY_NAME["docs-rs"]
        assert crates["order"] < docs_rs["order"], (
            f"crates-package ({crates['order']}) should be before docs-rs ({docs_rs['order']})"
        )

    def test_docs_rs_before_docs_page(self):
        """docs.rs is more specific than the generic docs-page template."""
        docs_rs = TEMPLATES_BY_NAME["docs-rs"]
        docs_page = TEMPLATES_BY_NAME["docs-page"]
        assert docs_rs["order"] < docs_page["order"], (
            f"docs-rs ({docs_rs['order']}) should be before docs-page ({docs_page['order']})"
        )

    def test_package_templates_before_search(self):
        """Package templates (npm, pypi, crates) should be before search templates."""
        search_orders = [
            TEMPLATES_BY_NAME["duckduckgo-search"]["order"],
            TEMPLATES_BY_NAME["google-search"]["order"],
        ]
        for name in ("npm-package", "pypi-package", "crates-package"):
            pkg_order = TEMPLATES_BY_NAME[name]["order"]
            for so in search_orders:
                assert pkg_order < so, (
                    f"{name} ({pkg_order}) should be before search templates ({so})"
                )

    def test_auto_detection_order_is_stable(self):
        """Sorted templates should be in ascending order."""
        for i in range(len(ALL_TEMPLATES) - 1):
            assert ALL_TEMPLATES[i]["order"] <= ALL_TEMPLATES[i + 1]["order"], (
                f"Templates not sorted: '{ALL_TEMPLATES[i]['name']}' "
                f"(order={ALL_TEMPLATES[i]['order']}) vs "
                f"'{ALL_TEMPLATES[i + 1]['name']}' "
                f"(order={ALL_TEMPLATES[i + 1]['order']})"
            )

    def test_docs_rs_not_matched_by_docs_page(self):
        """A docs.rs URL should match docs-rs first, not docs-page (ensuring ordering works)."""
        url = "https://docs.rs/solana/latest/solana/"

        matched = None
        for t in ALL_TEMPLATES:
            if "url_patterns" not in t:
                continue
            for pat in t["url_patterns"]:
                if re.search(pat, url):
                    matched = t["name"]
                    break
            if matched:
                break

        assert matched == "docs-rs", f"Expected docs-rs to match first for '{url}', got '{matched}'"

    def test_wikipedia_before_mdn(self):
        """Wikipedia is more specific (domain-based) than MDN."""
        wiki = TEMPLATES_BY_NAME["wikipedia"]
        mdn = TEMPLATES_BY_NAME["mdn-web-docs"]
        assert wiki["order"] < mdn["order"], (
            f"wikipedia ({wiki['order']}) should be before mdn-web-docs ({mdn['order']})"
        )

    def test_gitlab_after_github(self):
        """GitLab template should come after GitHub (lower priority)."""
        gh_issue = TEMPLATES_BY_NAME["github-issue"]
        gitlab = TEMPLATES_BY_NAME["gitlab"]
        assert gh_issue["order"] < gitlab["order"], (
            f"github-issue ({gh_issue['order']}) should be before gitlab ({gitlab['order']})"
        )

    def test_specific_templates_before_docs_page(self):
        """Specific templates that overlap with docs-page should precede it."""
        docs_page_order = TEMPLATES_BY_NAME["docs-page"]["order"]
        for name in ("docs-rs", "javadoc"):
            t = TEMPLATES_BY_NAME[name]
            assert t["order"] < docs_page_order, (
                f"{name} ({t['order']}) should be before docs-page ({docs_page_order})"
            )

    def test_new_content_templates_not_overlap_search_patterns(self):
        """Content templates' URL patterns must not accidentally match search engine URLs."""
        content_templates = [
            "wikipedia",
            "reddit",
            "mdn-web-docs",
            "gitlab",
            "devto",
            "go-pkg",
            "javadoc",
            "youtube",
        ]
        for name in content_templates:
            t = TEMPLATES_BY_NAME[name]
            for ct_pat in t.get("url_patterns", []):
                # A content template pattern should NOT match search engine URLs
                assert not re.search(ct_pat, "https://www.google.com/search?q=test"), (
                    f"{name} pattern '{ct_pat}' incorrectly matches Google search URL"
                )
                assert not re.search(ct_pat, "https://duckduckgo.com/?q=test"), (
                    f"{name} pattern '{ct_pat}' incorrectly matches DuckDuckGo search URL"
                )

    def test_wikipedia_not_matched_by_docs_page(self):
        """Wikipedia URL should match wikipedia first, not docs-page."""
        url = "https://en.wikipedia.org/wiki/Python_(programming_language)"

        matched = None
        for t in ALL_TEMPLATES:
            if "url_patterns" not in t:
                continue
            for pat in t["url_patterns"]:
                if re.search(pat, url):
                    matched = t["name"]
                    break
            if matched:
                break

        assert matched == "wikipedia", (
            f"Expected wikipedia to match first for '{url}', got '{matched}'"
        )

    def test_go_pkg_not_matched_by_docs_page(self):
        """pkg.go.dev should match go-pkg first, not docs-page."""
        url = "https://pkg.go.dev/fmt"

        matched = None
        for t in ALL_TEMPLATES:
            if "url_patterns" not in t:
                continue
            for pat in t["url_patterns"]:
                if re.search(pat, url):
                    matched = t["name"]
                    break
            if matched:
                break

        assert matched == "go-pkg", f"Expected go-pkg to match first for '{url}', got '{matched}'"

    def test_javadoc_not_matched_by_docs_page(self):
        """javadoc URL should match javadoc first, not docs-page."""
        url = (
            "https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/ArrayList.html"
        )

        matched = None
        for t in ALL_TEMPLATES:
            if "url_patterns" not in t:
                continue
            for pat in t["url_patterns"]:
                if re.search(pat, url):
                    matched = t["name"]
                    break
            if matched:
                break

        assert matched == "javadoc", f"Expected javadoc to match first for '{url}', got '{matched}'"


# ---------------------------------------------------------------------------
# 4. Section structure validation
# ---------------------------------------------------------------------------


class TestSectionStructure:
    def test_docs_rs_sections(self):
        t = TEMPLATES_BY_NAME["docs-rs"]
        section_names = {s["name"] for s in t["sections"]}
        assert "Crate" in section_names
        assert "Title" in section_names
        assert "Content" in section_names

        content_section = next(s for s in t["sections"] if s["name"] == "Content")
        assert content_section["format"] == "markdown"

    def test_docker_hub_sections(self):
        t = TEMPLATES_BY_NAME["docker-hub"]
        section_names = {s["name"] for s in t["sections"]}
        assert "Image" in section_names
        assert "Description" in section_names
        assert "Content" in section_names

        desc_section = next(s for s in t["sections"] if s["name"] == "Description")
        assert desc_section["format"] == "attribute"
        assert desc_section["attribute"] == "content"

    def test_docs_page_has_source_url(self):
        t = TEMPLATES_BY_NAME["docs-page"]
        assert "source_url" in t
        assert "{url}" in t["source_url"]

    @pytest.mark.parametrize("name", ["docs-rs", "docker-hub", "docs-page"])
    def test_template_has_remove_selectors(self, name):
        t = TEMPLATES_BY_NAME[name]
        assert "remove" in t
        assert isinstance(t["remove"], list)
        assert len(t["remove"]) > 0

    def test_docs_rs_remove_excludes_sidebar_nav(self):
        t = TEMPLATES_BY_NAME["docs-rs"]
        remove = t["remove"]
        assert "nav.sidebar" in remove or "nav" in remove
        assert "footer" in remove

    # --- New template section structure tests ---

    def test_wikipedia_sections(self):
        t = TEMPLATES_BY_NAME["wikipedia"]
        section_names = {s["name"] for s in t["sections"]}
        assert "Title" in section_names
        assert "Content" in section_names

        title_section = next(s for s in t["sections"] if s["name"] == "Title")
        assert title_section["required"] is True

        content_section = next(s for s in t["sections"] if s["name"] == "Content")
        assert content_section["format"] == "markdown"
        assert content_section["required"] is True

    def test_reddit_sections(self):
        t = TEMPLATES_BY_NAME["reddit"]
        section_names = {s["name"] for s in t["sections"]}
        assert "Title" in section_names
        assert "Author" in section_names
        assert "Content" in section_names
        assert "Link" in section_names
        assert "Subreddit" in section_names

        title_section = next(s for s in t["sections"] if s["name"] == "Title")
        assert title_section["required"] is True

        link_section = next(s for s in t["sections"] if s["name"] == "Link")
        assert link_section["format"] == "attribute"
        assert link_section["attribute"] == "href"

    def test_mdn_sections(self):
        t = TEMPLATES_BY_NAME["mdn-web-docs"]
        section_names = {s["name"] for s in t["sections"]}
        assert "Title" in section_names
        assert "Content" in section_names

        content_section = next(s for s in t["sections"] if s["name"] == "Content")
        assert content_section["format"] == "markdown"

    def test_gitlab_sections(self):
        t = TEMPLATES_BY_NAME["gitlab"]
        section_names = {s["name"] for s in t["sections"]}
        assert "Title" in section_names
        assert "Description" in section_names
        assert "README" in section_names

        desc_section = next(s for s in t["sections"] if s["name"] == "Description")
        assert desc_section["format"] == "attribute"
        assert desc_section["attribute"] == "content"

    def test_youtube_sections(self):
        t = TEMPLATES_BY_NAME["youtube"]
        section_names = {s["name"] for s in t["sections"]}
        assert "Title" in section_names
        assert "Channel" in section_names
        assert "Description" in section_names

        title_section = next(s for s in t["sections"] if s["name"] == "Title")
        assert title_section["format"] == "attribute"
        assert title_section["attribute"] == "content"
        assert title_section["required"] is True

    def test_devto_sections(self):
        t = TEMPLATES_BY_NAME["devto"]
        section_names = {s["name"] for s in t["sections"]}
        assert "Title" in section_names
        assert "Author" in section_names
        assert "Content" in section_names

        content_section = next(s for s in t["sections"] if s["name"] == "Content")
        assert content_section["format"] == "markdown"
        assert content_section["required"] is True

    def test_go_pkg_sections(self):
        t = TEMPLATES_BY_NAME["go-pkg"]
        section_names = {s["name"] for s in t["sections"]}
        assert "Title" in section_names
        assert "Overview" in section_names
        assert "Documentation" in section_names

    def test_javadoc_sections(self):
        t = TEMPLATES_BY_NAME["javadoc"]
        section_names = {s["name"] for s in t["sections"]}
        assert "Title" in section_names
        assert "Content" in section_names

        content_section = next(s for s in t["sections"] if s["name"] == "Content")
        assert content_section["format"] == "markdown"

    @pytest.mark.parametrize(
        "name",
        [
            "wikipedia",
            "reddit",
            "mdn-web-docs",
            "gitlab",
            "youtube",
            "devto",
            "go-pkg",
            "javadoc",
        ],
    )
    def test_new_template_has_remove_selectors(self, name):
        t = TEMPLATES_BY_NAME[name]
        assert "remove" in t
        assert isinstance(t["remove"], list)
        assert len(t["remove"]) > 0

    def test_wikipedia_header_not_removed(self):
        """Wikipedia h1 is inside <header> in Vector 2022 skin — ensure it's preserved."""
        t = TEMPLATES_BY_NAME["wikipedia"]
        assert "header" not in t["remove"], (
            "Wikipedia template must NOT remove <header> (h1 lives inside it)"
        )

    def test_devto_header_not_removed(self):
        """Dev.to article h1 may be inside a <header> element."""
        t = TEMPLATES_BY_NAME["devto"]
        assert "header" not in t["remove"], (
            "devto template must NOT remove <header> (h1 may live inside it)"
        )

    def test_raw_template_has_empty_remove(self):
        """Raw template must have empty remove list (not null, to avoid default removes)."""
        t = TEMPLATES_BY_NAME["raw"]
        assert "remove" in t
        assert t["remove"] == [], "raw template must have empty remove list to avoid any filtering"

    def test_raw_template_has_body_section(self):
        """Raw template extracts the full body as markdown."""
        t = TEMPLATES_BY_NAME["raw"]
        section_names = {s["name"] for s in t["sections"]}
        assert "Content" in section_names

        content = next(s for s in t["sections"] if s["name"] == "Content")
        assert content["selector"] == "body"
        assert content["format"] == "markdown"
        assert content["required"] is False  # graceful on empty pages

    def test_raw_template_high_order(self):
        """Raw template should have highest order (only used by explicit name)."""
        t = TEMPLATES_BY_NAME["raw"]
        assert t["order"] > 100, (
            "raw template should have high order (>= 100) to not interfere with auto-detection"
        )


# ---------------------------------------------------------------------------
# 5. Source URL resolution
# ---------------------------------------------------------------------------


class TestSourceUrlResolution:
    @staticmethod
    def resolve_source_url(source_template: str, url: str) -> str:
        if source_template == "{url}.md":
            return f"{url.rstrip('/')}.md"
        return source_template.replace("{url}", url)

    def test_docs_page_source_url_format(self):
        t = TEMPLATES_BY_NAME["docs-page"]
        url = "https://solana.com/docs/rpc/http/getslot"
        source = self.resolve_source_url(t["source_url"], url)
        assert source == "https://solana.com/docs/rpc/http/getslot.md"

    def test_source_url_resolution_with_trailing_slash(self):
        t = TEMPLATES_BY_NAME["docs-page"]
        url = "https://example.com/docs/api/"
        source = self.resolve_source_url(t["source_url"], url)
        assert source == "https://example.com/docs/api.md"

    def test_no_source_url_for_docs_rs(self):
        t = TEMPLATES_BY_NAME["docs-rs"]
        assert "source_url" not in t, "docs.rs does not have raw markdown endpoints"

    def test_no_source_url_for_docker_hub(self):
        t = TEMPLATES_BY_NAME["docker-hub"]
        assert "source_url" not in t, "Docker Hub does not have raw markdown endpoints"


# ---------------------------------------------------------------------------
# 6. Markdown content detection (unit tests for _is_markdown_content)
# ---------------------------------------------------------------------------


# Minimal standalone reimplementation of _is_markdown_content for testing
def _is_markdown_content(text: str) -> bool:
    if not text:
        return False
    html_tags = len(re.findall(r"<\w+[^>]*>", text))
    if html_tags > 3:
        return False
    patterns = [
        r"^#{1,6}\s+\S",
        r"\[.+?\]\(.+?\)",
        r"```\w*\n",
        r"^\s*[-*+]\s+\S",
        r"\*\*[^*]+\*\*",
        r"^>\s+\S",
    ]
    for pat in patterns:
        if re.search(pat, text, re.MULTILINE):
            return True
    return False


def _strip_source_markdown(content: str) -> str:
    content = re.sub(r"^@twoslash-cache:.*$", "", content, flags=re.MULTILINE)
    content = re.sub(r"\n{3,}", "\n\n", content)
    return content.strip()


class TestMarkdownDetection:
    def test_detects_heading(self):
        assert _is_markdown_content("# Hello World")

    def test_detects_link(self):
        assert _is_markdown_content("See [docs](https://example.com) for more.")

    def test_detects_code_fence(self):
        assert _is_markdown_content("```python\nprint('hello')\n```")

    def test_detects_unordered_list(self):
        assert _is_markdown_content("- item 1\n- item 2")

    def test_rejects_html(self):
        assert not _is_markdown_content("<html><body><h1>Hello</h1></body></html>")

    def test_rejects_empty_string(self):
        assert not _is_markdown_content("")

    def test_rejects_plain_text(self):
        assert not _is_markdown_content("Just some plain text without any markdown syntax.")

    def test_detects_bold(self):
        assert _is_markdown_content("This is **bold** text.")

    def test_detects_blockquote(self):
        assert _is_markdown_content("> This is a quote")

    def test_accepts_mixed_markdown(self):
        text = "# Hello\n\nThis is **bold** and [a link](https://example.com)."
        assert _is_markdown_content(text)


class TestTwoslashStripping:
    def test_strips_twoslash_cache_lines(self):
        content = (
            "# Title\n\nSome text\n@twoslash-cache: abcdef1234567890abcdef1234567890\nMore text"
        )
        result = _strip_source_markdown(content)
        assert "@twoslash-cache:" not in result
        assert "# Title" in result
        assert "More text" in result

    def test_strips_multiple_twoslash_lines(self):
        content = (
            "# Title\n"
            "@twoslash-cache: hash1\n"
            "content\n"
            "@twoslash-cache: hash2\n"
            "@twoslash-cache: hash3\n"
            "footer\n"
        )
        result = _strip_source_markdown(content)
        assert "@twoslash-cache:" not in result
        assert "# Title\n\ncontent\n\nfooter" == result

    def test_no_twoslash_unchanged(self):
        content = "# Title\n\nNormal content here."
        result = _strip_source_markdown(content)
        assert result == content

    def test_collapses_excess_blank_lines(self):
        content = "# Title\n\n\n\n\n\nContent\n\n\n\n\n\nFooter"
        result = _strip_source_markdown(content)
        assert "\n\n\n\n" not in result
        assert result == "# Title\n\nContent\n\nFooter"


# ---------------------------------------------------------------------------
# 7. Extraction from sample HTML (using BeautifulSoup, no browser)
# ---------------------------------------------------------------------------


def _has_bs4():
    try:
        import bs4  # noqa: F401

        return True
    except ImportError:
        return False


class TestHtmlExtraction:
    """Test template extraction against static HTML snippets."""

    DOCS_RS_STRUCT_HTML = """<!DOCTYPE html>
    <html>
    <head><title>KeyInfo in ladon - Rust</title></head>
    <body>
      <nav class="sidebar">
        <div class="sidebar-crate"><h2>ladon</h2></div>
        <ul class="sidebar-items"><li><a href="#">Modules</a></li></ul>
      </nav>
      <nav class="sub"><a href="#">Platform</a></nav>
      <div id="main-content">
        <h1 class="fqn">
          <span class="in-band">Struct ladon::KeyInfo</span>
        </h1>
        <div class="docblock">
          <p>A single derived key / address tuple.</p>
        </div>
        <h2 id="fields">Fields</h2>
        <span>index: u32</span>
        <span>path: String</span>
        <span>private_key: String</span>
        <span>public_key: String</span>
        <span>address: String</span>
      </div>
      <footer>docs.rs footer</footer>
    </body>
    </html>"""

    DOCS_RS_CRATE_HTML = """<!DOCTYPE html>
    <html>
    <head><title>solana - Rust</title></head>
    <body>
      <nav class="sidebar"><!-- sidebar content --></nav>
      <div id="main-content">
        <h1 class="crate-title">Crate solana</h1>
        <div class="docblock"><p>Blockchain, Rebuilt for Scale</p></div>
        <h2>Modules</h2>
        <ul><li>broadcast_stage</li><li>cluster_info</li></ul>
      </div>
      <footer>footer</footer>
    </body>
    </html>"""

    DOCKER_HUB_HTML = """<!DOCTYPE html>
    <html>
    <head>
      <title>postgres - Docker Hub</title>
      <meta name="description"
            content="PostgreSQL object-relational database with reliability and data integrity.">
    </head>
    <body>
      <header><!-- nav --></header>
      <main>
        <h1>postgres Docker official image overview</h1>
        <article>
          <h3>Quick reference</h3>
          <p>Maintained by the PostgreSQL Docker Community.</p>
          <h3>Supported tags</h3>
          <ul><li>18.4, 18, latest</li><li>17.10, 17</li></ul>
          <h2>How to use this image</h2>
          <pre><code>docker run postgres</code></pre>
        </article>
      </main>
      <footer>footer</footer>
    </body>
    </html>"""

    def _apply_remove(self, soup, template):
        for sel in template.get("remove", []):
            try:
                for tag in soup.select(sel):
                    if hasattr(tag, "decompose"):
                        tag.decompose()
            except Exception:
                pass

    def test_docs_rs_struct_extraction(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(self.DOCS_RS_STRUCT_HTML, "html.parser")
        t = TEMPLATES_BY_NAME["docs-rs"]

        self._apply_remove(soup, t)

        h1 = soup.select_one("h1.fqn, .in-band h1")
        assert h1 is not None
        assert "KeyInfo" in h1.get_text()

        main = soup.select_one("#main-content")
        assert main is not None
        assert "key / address" in main.get_text()

        footer = soup.select_one("footer")
        assert footer is None or footer.decomposed

    def test_docs_rs_crate_extraction(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(self.DOCS_RS_CRATE_HTML, "html.parser")
        t = TEMPLATES_BY_NAME["docs-rs"]

        self._apply_remove(soup, t)

        h1 = soup.select_one("h1.crate-title")
        assert h1 is not None
        assert "solana" in h1.get_text()

        main = soup.select_one("#main-content")
        assert main is not None

        sidebar = soup.select_one("nav.sidebar")
        assert sidebar is None or sidebar.decomposed

    def test_docker_hub_extraction(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(self.DOCKER_HUB_HTML, "html.parser")
        t = TEMPLATES_BY_NAME["docker-hub"]

        self._apply_remove(soup, t)

        h1 = soup.select_one("h1")
        assert h1 is not None
        assert "postgres" in h1.get_text()

        meta_desc = soup.select_one("meta[name='description']")
        assert meta_desc is not None
        assert "PostgreSQL" in meta_desc.get("content", "")

        article = soup.select_one("article")
        assert article is not None
        assert "Quick reference" in article.get_text()

        header = soup.select_one("header")
        assert header is None or header.decomposed

    # --- New template extraction fixtures ---

    WIKIPEDIA_HTML = """<!DOCTYPE html>
    <html>
    <head><title>Rust (programming language) - Wikipedia</title></head>
    <body>
      <nav id="mw-navigation"><!-- nav --></nav>
      <header class="mw-header">
        <div class="mw-page-title-namespace">Article</div>
        <h1 id="firstHeading" class="firstHeading">Rust (programming language)</h1>
      </header>
      <div id="mw-content-text">
        <div class="mw-parser-output">
          <p><b>Rust</b> is a general-purpose programming language.</p>
          <p>It emphasizes <a href="/wiki/Performance">performance</a> and safety.</p>
          <div class="mw-editsection"><!-- edit links --></div>
          <div class="navbox"><!-- navigation box --></div>
        </div>
      </div>
      <footer><!-- footer --></footer>
    </body>
    </html>"""

    MDN_HTML = """<!DOCTYPE html>
    <html>
    <head><title>Promise - JavaScript | MDN</title></head>
    <body>
      <nav class="sidebar"><!-- sidebar --></nav>
      <header><!-- header --></header>
      <article class="main-page-content">
        <h1>Promise</h1>
        <p>The <b>Promise</b> object represents the eventual completion
        of an asynchronous operation.</p>
        <div class="notecard"><p>A note</p></div>
      </article>
      <footer><!-- footer --></footer>
    </body>
    </html>"""

    REDDIT_HTML = """<!DOCTYPE html>
    <html>
    <head><title>Rust 1.96.1 is out : rust</title></head>
    <body>
      <header><!-- header --></header>
      <shreddit-post>
        <a href="/r/rust/">r/rust</a>
        <h1 slot="title">Rust 1.96.1 is out</h1>
        <a href="/user/manpacket/" slot="authorName">manpacket</a>
        <div slot="text-body"><p>A new Rust release is here!</p></div>
      </shreddit-post>
      <shreddit-ad-post>PROMOTED CONTENT</shreddit-ad-post>
      <footer><!-- footer --></footer>
    </body>
    </html>"""

    GITLAB_HTML = """<!DOCTYPE html>
    <html>
    <head>
      <title>GitLab · GitLab</title>
      <meta name="description" content="GitLab is the open-source DevSecOps platform">
    </head>
    <body>
      <nav class="navbar-gitlab"><!-- nav --></nav>
      <header class="navbar-gitlab"><!-- header --></header>
      <div class="project-home-panel">
        <h1>GitLab</h1>
        <div class="readme-holder">
          <div class="md"><h1>GitLab</h1><p>Open source software to collaborate on code.</p></div>
        </div>
      </div>
      <footer><!-- footer --></footer>
    </body>
    </html>"""

    YOUTUBE_HTML = """<!DOCTYPE html>
    <html>
    <head>
      <title>Rick Astley - Never Gonna Give You Up - YouTube</title>
      <meta name="title" content="Rick Astley - Never Gonna Give You Up (Official Video)">
    </head>
    <body>
      <ytd-guide-renderer><!-- sidebar --></ytd-guide-renderer>
      <div id="owner"><a href="/@RickAstleyYT">Rick Astley</a></div>
      <div id="description-inline-expander">
        <p>The official video for Never Gonna Give You Up by Rick Astley.</p>
      </div>
      <ytd-comments><!-- comments --></ytd-comments>
    </body>
    </html>"""

    DEVTO_HTML = """<!DOCTYPE html>
    <html>
    <head><title>JavaScript Visualized: Promises &amp; Async/Await - DEV Community</title></head>
    <body>
      <nav><!-- nav --></nav>
      <header class="crayons-article__header">
        <h1>JavaScript Visualized: Promises &amp; Async/Await</h1>
        <div class="crayons-article__header__meta">
          <a href="/lydiahallie">Lydia Hallie</a>
        </div>
      </header>
      <div id="article-body">
        <p>Ever had to deal with JS code that just didn't run the way you expected?</p>
        <p>There's a chance you were dealing with Promises!</p>
      </div>
      <div class="crayons-article-actions"><!-- reactions --></div>
      <footer><!-- footer --></footer>
    </body>
    </html>"""

    GO_PKG_HTML = """<!DOCTYPE html>
    <html>
    <head><title>fmt package - fmt - Go Packages</title></head>
    <body>
      <header class="go-Header"><!-- go header --></header>
      <nav class="go-Main-nav"><!-- nav --></nav>
      <h1>fmt</h1>
      <div class="Documentation-overview">
        <p>Package fmt implements formatted I/O with functions analogous
        to C's printf and scanf.</p>
      </div>
      <div class="Documentation-content">
        <h2>Functions</h2>
        <p>func Printf(format string, a ...interface{}) (n int, err error)</p>
      </div>
      <footer class="go-Footer"><!-- footer --></footer>
    </body>
    </html>"""

    JAVADOC_HTML = """<!DOCTYPE html>
    <html>
    <head><title>ArrayList - Java SE 21</title></head>
    <body>
      <div class="topNav"><!-- nav --></div>
      <div class="subNav"><!-- sub nav --></div>
      <div class="contentContainer">
        <h2 class="title">Class ArrayList&lt;E&gt;</h2>
        <div class="description">
          <div class="block">
            <p>Resizable-array implementation of the <code>List</code> interface.</p>
          </div>
        </div>
      </div>
      <div class="bottomNav"><!-- nav --></div>
    </body>
    </html>"""

    # --- New template extraction tests ---

    def test_wikipedia_extraction(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(self.WIKIPEDIA_HTML, "html.parser")
        t = TEMPLATES_BY_NAME["wikipedia"]

        self._apply_remove(soup, t)

        # h1 should survive (not in <header> which is NOT removed)
        h1 = soup.select_one("h1")
        assert h1 is not None
        assert "Rust (programming language)" in h1.get_text()

        # Content should be present
        content = soup.select_one(".mw-parser-output")
        assert content is not None
        assert "general-purpose" in content.get_text()

        # Noise should be removed
        editsection = soup.select_one(".mw-editsection")
        assert editsection is None or editsection.decomposed

        navbox = soup.select_one(".navbox")
        assert navbox is None or navbox.decomposed

    def test_mdn_extraction(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(self.MDN_HTML, "html.parser")
        t = TEMPLATES_BY_NAME["mdn-web-docs"]

        self._apply_remove(soup, t)

        h1 = soup.select_one("h1")
        assert h1 is not None
        assert "Promise" in h1.get_text()

        article = soup.select_one("article")
        assert article is not None
        assert "asynchronous operation" in article.get_text()

        # notecard should be removed
        notecard = soup.select_one(".notecard")
        assert notecard is None or notecard.decomposed

    def test_reddit_extraction(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(self.REDDIT_HTML, "html.parser")
        t = TEMPLATES_BY_NAME["reddit"]

        self._apply_remove(soup, t)

        # Title survives
        h1 = soup.select_one("h1")
        assert h1 is not None
        assert "Rust 1.96.1 is out" in h1.get_text()

        # Author survives
        author = soup.select_one("a[href*='/user/']")
        assert author is not None
        assert "manpacket" in author.get_text()

        # Content survives
        text_body = soup.select_one("[slot='text-body']")
        assert text_body is not None
        assert "new Rust release" in text_body.get_text()

        # Ads removed
        ad = soup.select_one("shreddit-ad-post")
        assert ad is None or ad.decomposed

    def test_gitlab_extraction(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(self.GITLAB_HTML, "html.parser")
        t = TEMPLATES_BY_NAME["gitlab"]

        self._apply_remove(soup, t)

        h1 = soup.select_one("h1")
        assert h1 is not None
        assert "GitLab" in h1.get_text()

        meta_desc = soup.select_one("meta[name='description']")
        assert meta_desc is not None
        assert "DevSecOps" in meta_desc.get("content", "")

        readme = soup.select_one(".readme-holder")
        assert readme is not None
        assert "collaborate" in readme.get_text()

    def test_youtube_extraction(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(self.YOUTUBE_HTML, "html.parser")
        t = TEMPLATES_BY_NAME["youtube"]

        self._apply_remove(soup, t)

        # Meta title survives
        meta_title = soup.select_one("meta[name='title']")
        assert meta_title is not None
        assert "Rick Astley" in meta_title.get("content", "")

        # Channel link survives
        channel = soup.select_one("#owner a")
        assert channel is not None
        assert "Rick Astley" in channel.get_text()

        # Description survives
        desc = soup.select_one("#description-inline-expander")
        assert desc is not None
        assert "official video" in desc.get_text()

        # Comments removed
        comments = soup.select_one("ytd-comments")
        assert comments is None or comments.decomposed

    def test_devto_extraction(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(self.DEVTO_HTML, "html.parser")
        t = TEMPLATES_BY_NAME["devto"]

        self._apply_remove(soup, t)

        h1 = soup.select_one("h1")
        assert h1 is not None
        assert "JavaScript Visualized" in h1.get_text()

        author = soup.select_one(".crayons-article__header__meta a")
        assert author is not None
        assert "Lydia Hallie" in author.get_text()

        body = soup.select_one("#article-body")
        assert body is not None
        assert "Promises" in body.get_text()

        # Actions removed
        actions = soup.select_one(".crayons-article-actions")
        assert actions is None or actions.decomposed

    def test_go_pkg_extraction(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(self.GO_PKG_HTML, "html.parser")
        t = TEMPLATES_BY_NAME["go-pkg"]

        self._apply_remove(soup, t)

        h1 = soup.select_one("h1")
        assert h1 is not None
        assert "fmt" in h1.get_text()

        overview = soup.select_one(".Documentation-overview")
        assert overview is not None
        assert "printf" in overview.get_text()

        docs = soup.select_one(".Documentation-content")
        assert docs is not None
        assert "Printf" in docs.get_text()

        # Header removed
        header = soup.select_one(".go-Header")
        assert header is None or header.decomposed

    def test_javadoc_extraction(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(self.JAVADOC_HTML, "html.parser")
        t = TEMPLATES_BY_NAME["javadoc"]

        self._apply_remove(soup, t)

        # Content container survives
        container = soup.select_one(".contentContainer")
        assert container is not None
        assert "ArrayList" in container.get_text()
        assert "Resizable-array" in container.get_text()

        # Nav removed
        top_nav = soup.select_one(".topNav")
        assert top_nav is None or top_nav.decomposed

        bottom_nav = soup.select_one(".bottomNav")
        assert bottom_nav is None or bottom_nav.decomposed

    def test_wikipedia_header_preserved_for_h1(self):
        """Vector 2022 skin puts h1 inside <header> — header must NOT be removed."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(self.WIKIPEDIA_HTML, "html.parser")
        t = TEMPLATES_BY_NAME["wikipedia"]

        self._apply_remove(soup, t)

        # The <header> element itself should still exist
        header = soup.select_one("header.mw-header")
        assert header is not None, "Wikipedia <header> must survive removal"
        assert not (hasattr(header, "decomposed") and header.decomposed)

        # And h1 inside it should be findable
        h1 = header.select_one("h1")
        assert h1 is not None, "h1 inside preserved <header> must be accessible"

    def test_raw_extraction(self):
        """Raw template: empty remove -> body content preserved with all elements."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(self.MDN_HTML, "html.parser")
        t = TEMPLATES_BY_NAME["raw"]

        self._apply_remove(soup, t)

        # Body should still exist
        body = soup.select_one("body")
        assert body is not None, "body must survive raw template"

        # Nav, header, footer should all survive (empty remove list)
        nav = soup.select_one("nav")
        assert nav is not None
        assert not (hasattr(nav, "decomposed") and nav.decomposed), "nav must survive raw template"

        header = soup.select_one("header")
        assert header is not None
        assert not (hasattr(header, "decomposed") and header.decomposed), (
            "header must survive raw template"
        )

        footer = soup.select_one("footer")
        assert footer is not None
        assert not (hasattr(footer, "decomposed") and footer.decomposed), (
            "footer must survive raw template"
        )

        # Content should be present
        h1 = soup.select_one("h1")
        assert h1 is not None
        assert "Promise" in h1.get_text()


# ---------------------------------------------------------------------------
# 8. searchfetch CLI smoke test (ensures the server can be imported)
# ---------------------------------------------------------------------------


class TestServerImport:
    def test_server_imports_without_error(self):
        import server  # noqa: F401

    def test_builtin_templates_loaded(self):
        import server

        tmpl_names = server.BUILTIN_TEMPLATES.keys()
        assert "docs-rs" in tmpl_names
        assert "docker-hub" in tmpl_names
        assert "docs-page" in tmpl_names
        assert "crates-package" in tmpl_names
        assert "wikipedia" in tmpl_names
        assert "reddit" in tmpl_names
        assert "mdn-web-docs" in tmpl_names
        assert "gitlab" in tmpl_names
        assert "youtube" in tmpl_names
        assert "devto" in tmpl_names
        assert "go-pkg" in tmpl_names
        assert "javadoc" in tmpl_names
        assert "raw" in tmpl_names

    def test_source_markdown_functions_exist(self):
        import server

        assert callable(server._is_markdown_content)
        assert callable(server._strip_source_markdown)
        assert callable(server._resolve_source_url)
        assert callable(server._fetch_source_markdown)

    def test_server_version_is_set(self):
        import server

        assert server.__version__ == "3.3.0"


# ---------------------------------------------------------------------------
# 9. Python template helper functions (unit tests)
# ---------------------------------------------------------------------------


class TestPythonTransforms:
    """Test Python transform functions from server module."""

    def test_strip_transform(self):
        import server

        assert server.apply_transform("  hello  ", "strip") == "hello"

    def test_decode_google_url(self):
        import server

        result = server.apply_transform("/url?q=https://example.com/page&sa=U", "decode_google_url")
        assert result == "https://example.com/page"

    def test_decode_google_url_non_match(self):
        import server

        result = server.apply_transform("https://example.com/page", "decode_google_url")
        assert result == "https://example.com/page"

    def test_decode_ddg_url(self):
        import server

        result = server.apply_transform(
            "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com",
            "decode_ddg_url",
        )
        assert result == "https://example.com"

    def test_json_parse(self):
        import server

        result = server.apply_transform('{"key": "value", "num": 42}', "json_parse")
        assert '"key"' in result
        assert '"value"' in result

    def test_resolve_href(self):
        import server

        result = server.apply_transform("/docs/api", "resolve_href", "https://example.com")
        assert result == "https://example.com/docs/api"

    def test_resolve_href_absolute_unchanged(self):
        import server

        result = server.apply_transform(
            "https://other.com/page", "resolve_href", "https://example.com"
        )
        assert result == "https://other.com/page"

    def test_transform_chain(self):
        import server

        # chain: strip first, then other transforms
        result = server.apply_transform("  hello  ", ["strip", "strip"])
        assert result == "hello"


class TestPythonUrlTemplateResolution:
    """Test Python resolve_url_template function."""

    def test_resolves_simple_placeholders(self):
        import server

        tmpl = {
            "name": "test",
            "url_template": "https://example.com/{path}",
            "url_params": {"path": {"default": "home"}},
        }
        url = server.resolve_url_template(tmpl, {"path": "about"})
        assert url == "https://example.com/about"

    def test_uses_default_values(self):
        import server

        tmpl = {
            "name": "test",
            "url_template": "https://example.com/{page}",
            "url_params": {"page": {"default": "index"}},
        }
        url = server.resolve_url_template(tmpl, {})
        assert url == "https://example.com/index"

    def test_throws_for_missing_required(self):
        import server

        tmpl = {
            "name": "test",
            "url_template": "https://example.com/{query}",
            "url_params": {"query": {"required": True}},
        }
        with pytest.raises(ValueError, match="Required URL parameter"):
            server.resolve_url_template(tmpl, {})

    def test_url_encodes_when_specified(self):
        import server

        tmpl = {
            "name": "test",
            "url_template": "https://example.com/?q={query}",
            "url_params": {"query": {"encode": "url"}},
        }
        url = server.resolve_url_template(tmpl, {"query": "hello world"})
        # quote_plus uses + for spaces
        assert "hello" in url
        assert "%20" in url or "+" in url


class TestPythonSearchParamMapping:
    """Test Python map_search_params function."""

    def test_duckduckgo_region(self):
        import server

        params = server.map_search_params("test query", "duckduckgo", "us-en", None)
        assert params["query"] == "test query"
        assert params["kl"] == "us-en"

    def test_duckduckgo_safe_search_on(self):
        import server

        params = server.map_search_params("test", "duckduckgo", None, True)
        assert params["kp"] == "1"

    def test_duckduckgo_safe_search_off(self):
        import server

        params = server.map_search_params("test", "duckduckgo", None, False)
        assert params["kp"] == "-2"

    def test_google_region(self):
        import server

        params = server.map_search_params("test query", "google", "us-en", None)
        assert params["query"] == "test query"
        assert params["hl"] == "us"
        assert params["gl"] == "en"

    def test_google_safe_search(self):
        import server

        params = server.map_search_params("test", "google", None, True)
        assert params["safe"] == "active"

    def test_no_region_or_safesearch_when_null(self):
        import server

        params = server.map_search_params("test", "duckduckgo", None, None)
        assert params["query"] == "test"
        assert "kl" not in params
        assert "kp" not in params


class TestPythonComposition:
    """Test Python compose_sections and compose_search_results functions."""

    def test_compose_sections_empty(self):
        import server

        result = server.compose_sections([])
        assert "(No content extracted" in result

    def test_compose_sections_value(self):
        import server

        sections = [
            {"name": "Title", "type": "value", "value": "Hello World"},
        ]
        result = server.compose_sections(sections)
        assert "## Title" in result
        assert "Hello World" in result

    def test_compose_sections_multiple(self):
        import server

        sections = [
            {"name": "Items", "type": "multiple", "items": ["A", "B", "C"]},
        ]
        result = server.compose_sections(sections)
        assert "## Items" in result
        assert "A" in result
        assert "B" in result
        assert "C" in result

    def test_compose_search_results_formats_numbered(self):
        import server

        sections = [
            {
                "name": "Results",
                "type": "search_results",
                "items": [
                    {
                        "Title": "Test Page",
                        "URL": "https://example.com",
                        "Snippet": "A description",
                    },
                ],
            },
        ]
        result = server.compose_search_results(sections)
        assert "[1] Test Page" in result
        assert "URL: https://example.com" in result
        assert "Snippet: A description" in result

    def test_compose_search_results_filters_non_http(self):
        import server

        sections = [
            {
                "name": "Results",
                "type": "search_results",
                "items": [
                    {"Title": "Internal", "URL": "/internal", "Snippet": ""},
                ],
            },
        ]
        result = server.compose_search_results(sections)
        assert "URL:" not in result

    def test_compose_search_results_filters_google_internal(self):
        import server

        sections = [
            {
                "name": "Results",
                "type": "search_results",
                "items": [
                    {
                        "Title": "Google Link",
                        "URL": "https://www.google.com/search?q=test",
                        "Snippet": "desc",
                    },
                ],
            },
        ]
        result = server.compose_search_results(sections)
        assert "URL:" not in result

    def test_compose_search_results_falls_back_to_sections(self):
        import server

        sections = [
            {"name": "Title", "type": "value", "value": "Fallback content"},
        ]
        result = server.compose_search_results(sections)
        assert "Fallback content" in result


class TestPythonPageTemplateResolution:
    """Test Python resolve_page_template function."""

    def test_auto_detects_by_url(self):
        import server

        tmpl, name = server.resolve_page_template("https://docs.rs/tokio/latest/tokio/", "auto")
        assert tmpl is not None
        assert name == "docs-rs"

    def test_named_builtin(self):
        import server

        tmpl, name = server.resolve_page_template("https://example.com/some-page", "npm-package")
        assert tmpl is not None
        assert name == "npm-package"

    def test_returns_fallback_for_unmatched_auto(self):
        import server

        tmpl, name = server.resolve_page_template("https://example.com/random-page", "auto")
        assert tmpl is None
        assert "fallback" in name.lower()

    def test_throws_for_unknown_named_template(self):
        import server

        with pytest.raises(ValueError, match="Unknown template"):
            server.resolve_page_template("https://example.com", "nonexistent-template")

    def test_inline_json_template(self):
        import server

        tmpl, name = server.resolve_page_template(
            "https://example.com",
            '{"name": "custom", "sections": [], "url_patterns": []}',
        )
        assert tmpl is not None
        assert tmpl["name"] == "custom"
        assert name == "custom"

    def test_inline_json_parse_error(self):
        import server

        with pytest.raises(ValueError, match="Invalid inline JSON"):
            server.resolve_page_template("https://example.com", "{not valid json")


class TestPythonSelectHelpers:
    """Test Python _select_first and _select_one_first utility functions."""

    def test_select_first_with_results(self):
        from bs4 import BeautifulSoup

        import server

        soup = BeautifulSoup("<div><p>Hello</p><p>World</p></div>", "html.parser")
        results = server._select_first(soup, "p")
        assert len(results) == 2
        assert results[0].get_text() == "Hello"

    def test_select_first_empty_selector_returns_parent(self):
        from bs4 import BeautifulSoup

        import server

        soup = BeautifulSoup("<div>content</div>", "html.parser")
        results = server._select_first(soup, "")
        assert len(results) == 1
        assert results[0] is soup

    def test_select_first_no_results(self):
        from bs4 import BeautifulSoup

        import server

        soup = BeautifulSoup("<div><p>Hello</p></div>", "html.parser")
        results = server._select_first(soup, "h1")
        assert len(results) == 0

    def test_select_one_first(self):
        from bs4 import BeautifulSoup

        import server

        soup = BeautifulSoup("<div><p>Hello</p></div>", "html.parser")
        result = server._select_one_first(soup, "p")
        assert result is not None
        assert result.get_text() == "Hello"


class TestPythonMetadata:
    """Test Python server version and metadata."""

    def test_version_is_3_2_3(self):
        import server

        assert server.__version__ == "3.3.0"

    def test_server_name_is_searchfetch(self):
        import server

        assert server.mcp.name == "searchfetch"
