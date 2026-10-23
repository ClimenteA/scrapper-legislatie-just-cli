import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scrapper_legislatie_just.cli import app
from scrapper_legislatie_just.cleaner import LegislatieJustCleaner
from scrapper_legislatie_just.config import DATA_ROOT_NAME, STEP_DIRS
from scrapper_legislatie_just.downloader import (
    LinkuriConsolidate,
    LegislatieJustDownloader,
    MonolithNotFoundError,
)
from scrapper_legislatie_just.expander import LegislatieJustExpander
from scrapper_legislatie_just.misc import extract_file_meta
from scrapper_legislatie_just.sources import (
    SOURCES_TOML_PATH,
    load_sources,
    surse_legislatie_just,
)

FIXTURES = Path(__file__).parent / "fixtures"
SLUG = "legea-contabilitatii"


def _source():
    return next(s for s in surse_legislatie_just if s.slug == SLUG)


def test_cli_lists_sources():
    runner = CliRunner()
    result = runner.invoke(app, ["list-sources"])

    assert result.exit_code == 0
    assert SLUG in result.stdout
    assert "codul-fiscal" in result.stdout


def test_cli_paths(tmp_path):
    runner = CliRunner()
    result = runner.invoke(app, ["paths", "--data-dir", str(tmp_path)])

    assert result.exit_code == 0
    for name in STEP_DIRS.values():
        assert str(tmp_path / name) in result.stdout
        assert (tmp_path / name).is_dir()


def test_default_data_dir_is_current_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SCRAPPER_DATA_DIR", raising=False)

    runner = CliRunner()
    result = runner.invoke(app, ["paths"])

    data_root = tmp_path.resolve() / DATA_ROOT_NAME
    assert str(data_root) in result.stdout
    for name in STEP_DIRS.values():
        assert str(data_root / name) in result.stdout
        assert (data_root / name).is_dir()


def test_downloader_helpers(tmp_path):
    downloader = LegislatieJustDownloader(surse=[], data_dir=tmp_path)

    assert (
        downloader.creeaza_link_complet("~/../../../Public/DetaliiDocument/307832")
        == "https://legislatie.just.ro/Public/DetaliiDocument/307832"
    )
    assert downloader.output_dir == tmp_path / STEP_DIRS["raw"]


def test_download_requires_monolith(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "scrapper_legislatie_just.downloader.shutil.which", lambda name: None
    )
    downloader = LegislatieJustDownloader(surse=[_source()], data_dir=tmp_path)

    with pytest.raises(MonolithNotFoundError, match="monolith is required"):
        downloader.download()

    # Dry-run only discovers URLs and does not need monolith.
    downloader.dry_run = True
    monkeypatch.setattr(
        downloader, "preia_linkuri_consolidate", lambda url: []
    )
    assert downloader.download() == {
        "downloaded": 0,
        "failed": 0,
        "failed_urls": [],
    }


def test_downloader_dry_run_without_network(tmp_path, monkeypatch):
    """Step 1 discovery logic can be tested without hitting the website."""
    downloader = LegislatieJustDownloader(
        surse=[_source()], data_dir=tmp_path, limit=1, dry_run=True
    )
    monkeypatch.setattr(
        downloader,
        "preia_linkuri_consolidate",
        lambda url: [
            LinkuriConsolidate(
                link_sursa="https://legislatie.just.ro/Public/DetaliiDocument/1576",
                data_consolidarii="2024-01-15",
            )
        ],
    )

    result = downloader.download()

    assert result["downloaded"] == 1
    assert result["failed"] == 0
    assert list((tmp_path / STEP_DIRS["raw"]).glob("*.html")) == []


def _consolidari():
    return [
        LinkuriConsolidate(
            link_sursa="https://legislatie.just.ro/Public/DetaliiDocument/1576",
            data_consolidarii="2024-01-15",
        ),
        LinkuriConsolidate(
            link_sursa="https://legislatie.just.ro/Public/DetaliiDocument/1577",
            data_consolidarii="2024-06-20",
        ),
    ]


def test_downloader_keeps_only_latest_consolidation(tmp_path, monkeypatch):
    downloader = LegislatieJustDownloader(
        surse=[_source()], data_dir=tmp_path, dry_run=True
    )
    monkeypatch.setattr(
        downloader, "preia_linkuri_consolidate", lambda url: _consolidari()
    )

    latest = downloader.preia_cel_mai_recent_link(_source().link)

    assert latest is not None
    assert latest.data_consolidarii == "2024-06-20"
    assert downloader.download()["downloaded"] == 1


def test_downloader_all_versions_flag(tmp_path, monkeypatch):
    downloader = LegislatieJustDownloader(
        surse=[_source()], data_dir=tmp_path, dry_run=True, all_versions=True
    )
    monkeypatch.setattr(
        downloader, "preia_linkuri_consolidate", lambda url: _consolidari()
    )

    assert downloader.download()["downloaded"] == 2


def test_downloader_skips_only_the_same_version(tmp_path, monkeypatch):
    raw_dir = tmp_path / STEP_DIRS["raw"]
    raw_dir.mkdir(parents=True)
    (raw_dir / f"1576-{SLUG}-2024-01-15.html").write_text("old")

    downloader = LegislatieJustDownloader(
        surse=[_source()], data_dir=tmp_path, dry_run=True
    )
    monkeypatch.setattr(
        downloader, "preia_linkuri_consolidate", lambda url: _consolidari()
    )

    # An older local version must not block the latest consolidation.
    assert downloader.download()["downloaded"] == 1

    (raw_dir / f"1577-{SLUG}-2024-06-20.html").write_text("latest")
    assert downloader.download()["downloaded"] == 0


def test_sources_are_loaded_from_toml():
    assert SOURCES_TOML_PATH.exists()
    assert SOURCES_TOML_PATH.suffix == ".toml"

    sources = load_sources()
    assert len(sources) >= 30
    assert len(sources) == len(surse_legislatie_just)

    source = next(s for s in sources if s.slug == SLUG)
    assert source.numar_link == 1576
    assert source.link == "https://legislatie.just.ro/Public/DetaliiDocument/1576"
    assert source.nume == "LEGEA CONTABILITATII NR. 82/1991"


def test_load_sources_from_custom_toml(tmp_path):
    path = tmp_path / "custom-sources.toml"
    path.write_text(
        """
[[sources]]
numar_link = 123
nume = "Legea testului"
slug = "legea-testului"
descriere = "O descriere."
""",
        encoding="utf-8",
    )

    sources = load_sources(path)

    assert len(sources) == 1
    assert sources[0].slug == "legea-testului"
    assert sources[0].link.endswith("/123")
    assert sources[0].nume == "LEGEA TESTULUI"


def test_load_sources_prefers_sources_toml_from_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv("SCRAPPER_SOURCES_FILE", raising=False)
    local_sources = tmp_path / "sources.toml"
    local_sources.write_text(
        """
[[sources]]
numar_link = 999
nume = "Sursa locala"
slug = "sursa-locala"
descriere = "Fisier local."
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    sources = load_sources()

    assert len(sources) == 1
    assert sources[0].slug == "sursa-locala"


def test_sources_env_var_overrides_cwd_file(tmp_path, monkeypatch):
    local_sources = tmp_path / "sources.toml"
    local_sources.write_text(
        """
[[sources]]
numar_link = 1
nume = "Din cwd"
slug = "din-cwd"
descriere = "Din cwd."
""",
        encoding="utf-8",
    )
    env_sources = tmp_path / "env-sources.toml"
    env_sources.write_text(
        """
[[sources]]
numar_link = 2
nume = "Din env"
slug = "din-env"
descriere = "Din env."
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SCRAPPER_SOURCES_FILE", str(env_sources))

    sources = load_sources()

    assert len(sources) == 1
    assert sources[0].slug == "din-env"


def test_load_sources_rejects_duplicate_slugs(tmp_path):
    path = tmp_path / "duplicate-sources.toml"
    path.write_text(
        """
[[sources]]
numar_link = 1
nume = "Prima"
slug = "acelasi-slug"
descriere = "Prima."

[[sources]]
numar_link = 2
nume = "A doua"
slug = "acelasi-slug"
descriere = "A doua."
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Duplicate source slug"):
        load_sources(path)


def test_extract_file_meta():
    meta = extract_file_meta("1576-legea-contabilitatii-2024-01-15.html")

    assert meta == {
        "numar_link": "1576",
        "slug": SLUG,
        "data_consolidarii": "2024-01-15",
    }
    assert extract_file_meta("not-a-valid-name.html") is None


def test_clean_and_expand_pipeline(tmp_path):
    """Run steps 2-3 on two small fixtures (no network)."""
    raw_dir = tmp_path / STEP_DIRS["raw"]
    raw_dir.mkdir(parents=True)

    for fixture in FIXTURES.glob("*.html"):
        shutil.copy2(fixture, raw_dir / fixture.name)

    src = _source()

    # Step 2: clean.
    cleaner = LegislatieJustCleaner(
        surse=[src], data_dir=tmp_path, use_ocr=False
    )
    clean_result = cleaner.cleanify()

    assert clean_result == {"cleaned": 2, "skipped": 0}
    clean_files = sorted((tmp_path / STEP_DIRS["clean"]).glob("*.html"))
    assert len(clean_files) == 2

    cleaned_html = clean_files[0].read_text()
    assert "Cuprins" in cleaned_html
    assert "<article" in cleaned_html
    assert "S_BOOK_COVER" in cleaned_html
    assert "Prezentul act" in cleaned_html
    assert "Abrogat" not in cleaned_html

    # Running again skips already cleaned files.
    assert cleaner.cleanify() == {"cleaned": 0, "skipped": 2}

    # Step 3: expand (copy + TOC + Markdown conversion).
    expander = LegislatieJustExpander(surse=[src], data_dir=tmp_path)
    expand_result = expander.expand()

    assert expand_result["copied"] == 2
    assert expand_result["tocs"] == 1
    assert expand_result["converted"] == 2

    toc_path = tmp_path / STEP_DIRS["expanded"] / SLUG / f"toc-{SLUG}.json"
    toc = json.loads(toc_path.read_text())
    assert len(toc["consolidari"]) == 2

    md_files = sorted((tmp_path / STEP_DIRS["expanded"] / SLUG).glob("*.md"))
    assert len(md_files) == 2
    assert any("Prezentul act" in path.read_text() for path in md_files)


def test_slug_filter(tmp_path):
    """A command restricted to one slug must ignore files from other sources."""
    raw_dir = tmp_path / STEP_DIRS["raw"]
    raw_dir.mkdir(parents=True)
    fixture = next(FIXTURES.glob("*.html"))
    shutil.copy2(fixture, raw_dir / fixture.name)

    other_source = next(
        s for s in surse_legislatie_just if s.slug == "codul-fiscal"
    )
    cleaner = LegislatieJustCleaner(
        surse=[other_source], data_dir=tmp_path, use_ocr=False
    )

    assert cleaner.cleanify() == {"cleaned": 0, "skipped": 0}
    assert list((tmp_path / STEP_DIRS["clean"]).glob("*.html")) == []
