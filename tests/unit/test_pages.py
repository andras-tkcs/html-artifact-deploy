"""html_artifact_deploy.pages: page ids, titles and links."""

from __future__ import annotations

import pytest

from html_artifact_deploy.pages import PAGE_ID_RE, clean_title, is_page_id, new_page_id, page_url

pytestmark = pytest.mark.unit

GOOD_ID = "0123456789abcdef0123456789abcdef"


class TestNewPageId:
    def test_matches_the_page_id_pattern(self) -> None:
        assert PAGE_ID_RE.fullmatch(new_page_id())

    def test_a_thousand_ids_are_distinct(self) -> None:
        assert len({new_page_id() for _ in range(1000)}) == 1000


class TestIsPageId:
    def test_accepts_lowercase_hex(self) -> None:
        assert is_page_id(GOOD_ID)

    @pytest.mark.parametrize(
        "value",
        [GOOD_ID.upper(), GOOD_ID[:31], GOOD_ID + "0", GOOD_ID + "\n", "", "g" * 32],
    )
    def test_refuses_anything_else(self, value: str) -> None:
        assert not is_page_id(value)


class TestCleanTitle:
    def test_strips(self) -> None:
        assert clean_title("  Q3 report \n") == "Q3 report"

    def test_accepts_the_longest_title(self) -> None:
        assert clean_title("x" * 200) == "x" * 200

    @pytest.mark.parametrize("title", ["", "   ", "x" * 201])
    def test_rejects_empty_and_too_long(self, title: str) -> None:
        with pytest.raises(ValueError, match="title must be 1 to 200 characters."):
            clean_title(title)


class TestPageUrl:
    def test_joins_base_id_and_slash(self) -> None:
        assert page_url("https://pages.example.com", GOOD_ID) == f"https://pages.example.com/{GOOD_ID}/"

    def test_refuses_a_bad_id(self) -> None:
        with pytest.raises(ValueError):
            page_url("https://pages.example.com", "../etc")
