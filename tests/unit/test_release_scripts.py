"""scripts/: the release and coverage gates.

These scripts guard a release against failures that are silent otherwise (notes missing from a
green build, a skipped version, coverage quietly dropping), so their refusals are tested as
carefully as their happy paths.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


changelog_section = _load("changelog_section")
tag_release = _load("tag_release")
release_channel = _load("release_channel")
check_coverage_floor = _load("check_coverage_floor")

CHANGELOG = """# Changelog

<!-- ## [Unreleased] quoted inside a comment must not count -->

## [Unreleased]

## [1.1.0] — 2026-02-01

### Added

- Something.

## [1.0.0] — 2026-01-01

- First.
"""


class TestChangelogSection:
    def test_returns_the_body_without_its_heading(self) -> None:
        assert changelog_section.section(CHANGELOG, "1.1.0") == "### Added\n\n- Something."

    def test_tolerates_a_leading_v(self) -> None:
        assert changelog_section.section(CHANGELOG, "v1.0.0") == "- First."

    def test_missing_section_raises(self) -> None:
        with pytest.raises(LookupError, match="no section"):
            changelog_section.section(CHANGELOG, "9.9.9")

    def test_duplicated_section_raises(self) -> None:
        """A section opened early plus a renamed [Unreleased] would ship only the first half."""
        doubled = CHANGELOG + "\n## [1.1.0] — 2026-02-02\n\n- Other half.\n"
        with pytest.raises(LookupError, match="2 headings"):
            changelog_section.section(doubled, "1.1.0")

    def test_populated_unreleased_fails_the_cli(self, tmp_path: Path) -> None:
        """Entries stranded in [Unreleased] would be missing from the release notes."""
        path = tmp_path / "CHANGELOG.md"
        path.write_text(CHANGELOG.replace("## [Unreleased]\n", "## [Unreleased]\n\n- Pending.\n"))
        assert changelog_section.main(["1.1.0", "--changelog", str(path)]) == 1
        assert changelog_section.main(["1.1.0", "--changelog", str(path), "--allow-unreleased"]) == 0


class TestTagSequence:
    Identity = tag_release.Identity

    def _known(self, *tags: str) -> list:
        return [tag_release._parse_existing_tag(tag) for tag in tags]

    def test_first_prerelease_of_a_line_is_1(self) -> None:
        tag_release.check_sequential(tag_release.parse_version("1.1.0a1"), self._known("v1.0.0"))

    def test_skipped_prerelease_number_is_refused(self) -> None:
        with pytest.raises(tag_release.TagError, match="must be a2"):
            tag_release.check_sequential(tag_release.parse_version("1.1.0a3"), self._known("v1.0.0", "v1.1.0a1"))

    def test_skipped_version_is_refused(self) -> None:
        with pytest.raises(tag_release.TagError, match="no version may be skipped"):
            tag_release.check_sequential(tag_release.parse_version("1.2.0"), self._known("v1.0.0"))

    def test_stable_after_its_prereleases_is_allowed(self) -> None:
        tag_release.check_sequential(tag_release.parse_version("1.1.0"), self._known("v1.0.0", "v1.1.0rc1"))

    def test_retagging_a_stable_version_is_refused(self) -> None:
        with pytest.raises(tag_release.TagError, match="already tagged"):
            tag_release.check_sequential(tag_release.parse_version("1.0.0"), self._known("v1.0.0"))

    @pytest.mark.parametrize("bad", ["1.0", "1.0.0-beta1", "v1.0.0.post1", "latest"])
    def test_non_canonical_versions_are_refused(self, bad: str) -> None:
        with pytest.raises(tag_release.TagError):
            tag_release.parse_version(bad)


class TestReleaseChannel:
    @pytest.mark.parametrize(
        ("version", "expected"),
        [("1.2.0", "stable"), ("v1.2.0", "stable"), ("1.2.0a1", "alpha"), ("1.2.0b2", "beta"), ("1.2.0rc1", "rc")],
    )
    def test_channel(self, version: str, expected: str) -> None:
        assert release_channel.channel(version) == expected

    def test_garbage_is_an_error(self) -> None:
        assert release_channel.main(["1.2"]) == 1


class TestCoverageFloor:
    def _report(self, overall: float, per_file: dict[str, float]) -> dict:
        return {
            "totals": {"percent_covered": overall},
            "files": {path: {"summary": {"percent_covered": pct}} for path, pct in per_file.items()},
        }

    def test_passes_at_the_floor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(check_coverage_floor, "OVERALL_FLOOR", 90.0)
        monkeypatch.setattr(check_coverage_floor, "MODULE_FLOORS", {"a.py": 95.0})
        assert check_coverage_floor.check(self._report(90.0, {"a.py": 95.0})) == []

    def test_fails_below_a_module_floor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(check_coverage_floor, "OVERALL_FLOOR", 0.0)
        monkeypatch.setattr(check_coverage_floor, "MODULE_FLOORS", {"a.py": 95.0})
        assert check_coverage_floor.check(self._report(99.0, {"a.py": 94.9}))

    def test_a_module_missing_from_the_report_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A renamed module would otherwise keep a floor that protects nothing."""
        monkeypatch.setattr(check_coverage_floor, "OVERALL_FLOOR", 0.0)
        monkeypatch.setattr(check_coverage_floor, "MODULE_FLOORS", {"gone.py": 50.0})
        assert check_coverage_floor.check(self._report(99.0, {}))
