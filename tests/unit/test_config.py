"""Config loading, precedence, and tolerance of bad input."""

from __future__ import annotations

from pathlib import Path

from docrot.config import load_config
from docrot.model import Severity


def write(root: Path, name: str, body: str) -> None:
    (root / name).write_text(body, encoding="utf-8")


class TestLoading:
    def test_defaults_without_any_config(self, tmp_path: Path) -> None:
        cfg = load_config(tmp_path)
        assert cfg.temporal == "auto"
        assert cfg.fail_on is Severity.ERROR
        assert cfg.enabled_packs == ("core", "agents")
        assert cfg.docs == ("**/*.md", "**/*.rst")
        assert cfg.warnings == ()

    def test_pyproject_table(self, tmp_path: Path) -> None:
        write(
            tmp_path,
            "pyproject.toml",
            """
            [tool.docrot]
            packages = ["mypkg"]
            exclude_docs = ["docs/archive/**"]
            temporal = "off"
            strict = true
            external_packages = ["werkzeug"]
            ignore_refs = ["flask.request.*"]
            fail_on = "warning"
            severity = { PATH002 = "ignore" }
            rules = { enable = ["core"], disable = ["LINK001"] }
            """,
        )
        cfg = load_config(tmp_path)
        assert cfg.packages == ("mypkg",)
        assert cfg.exclude_docs == ("docs/archive/**",)
        assert cfg.temporal == "off"
        assert cfg.strict is True
        assert cfg.external_packages == ("werkzeug",)
        assert cfg.fail_on is Severity.WARNING
        assert cfg.severity_overrides["PATH002"] is Severity.IGNORE
        assert cfg.enabled_packs == ("core",)
        assert cfg.disabled_rules == ("LINK001",)

    def test_docrot_toml_overrides_pyproject(self, tmp_path: Path) -> None:
        write(tmp_path, "pyproject.toml", '[tool.docrot]\ntemporal = "off"\n')
        write(tmp_path, "docrot.toml", 'temporal = "on"\n')
        assert load_config(tmp_path).temporal == "on"

    def test_severity_keys_are_case_insensitive(self, tmp_path: Path) -> None:
        write(tmp_path, "docrot.toml", 'severity = { py002 = "warning" }\n')
        assert load_config(tmp_path).severity_overrides["PY002"] is Severity.WARNING


class TestTolerance:
    """Config must never be the reason a CI run explodes."""

    def test_unknown_key_warns_and_continues(self, tmp_path: Path) -> None:
        write(tmp_path, "docrot.toml", "nonsense_key = 1\n")
        cfg = load_config(tmp_path)
        assert any("nonsense_key" in w for w in cfg.warnings)
        assert cfg.temporal == "auto"

    def test_invalid_severity_warns(self, tmp_path: Path) -> None:
        write(tmp_path, "docrot.toml", 'severity = { PY002 = "catastrophic" }\n')
        assert any("invalid severity" in w for w in load_config(tmp_path).warnings)

    def test_invalid_fail_on_warns(self, tmp_path: Path) -> None:
        write(tmp_path, "docrot.toml", 'fail_on = "whenever"\n')
        assert any("invalid fail_on" in w for w in load_config(tmp_path).warnings)

    def test_invalid_temporal_warns_and_keeps_default(self, tmp_path: Path) -> None:
        write(tmp_path, "docrot.toml", 'temporal = "sometimes"\n')
        cfg = load_config(tmp_path)
        assert any("temporal must be" in w for w in cfg.warnings)
        assert cfg.temporal == "auto"

    def test_malformed_toml_is_ignored(self, tmp_path: Path) -> None:
        write(tmp_path, "docrot.toml", "this is not = = toml [[[\n")
        assert load_config(tmp_path).temporal == "auto"

    def test_retired_cache_dir_key_warns(self, tmp_path: Path) -> None:
        # the option was removed; it must not be silently accepted
        write(tmp_path, "docrot.toml", 'cache_dir = ".docrot_cache"\n')
        assert any("cache_dir" in w for w in load_config(tmp_path).warnings)


class TestRefIgnored:
    def test_glob_matching(self, tmp_path: Path) -> None:
        write(tmp_path, "docrot.toml", 'ignore_refs = ["flask.request.*", "exact.name"]\n')
        cfg = load_config(tmp_path)
        assert cfg.ref_ignored("flask.request.args")
        assert cfg.ref_ignored("exact.name")
        assert not cfg.ref_ignored("flask.Flask.route")
