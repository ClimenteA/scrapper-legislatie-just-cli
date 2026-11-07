import json
import shutil
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
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
        "orphans_removed": 0,
        "index_invalidated": False,
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


def test_downloader_falls_back_to_current_date_without_history(tmp_path, monkeypatch):
    """Sources without a consolidation history must still yield a date-only value."""
    downloader = LegislatieJustDownloader(surse=[_source()], data_dir=tmp_path)
    monkeypatch.setattr(
        downloader,
        "get_bs_soup",
        lambda url: BeautifulSoup("<html><body></body></html>", "html5lib"),
    )

    latest = downloader.preia_cel_mai_recent_link(_source().link)

    assert latest is not None
    assert latest.data_consolidarii == date.today().isoformat()
    assert latest.fara_data_consolidare is True


def test_downloader_skips_fallback_duplicate_by_content_hash(tmp_path, monkeypatch):
    """Sources without history must not be saved again when the text is unchanged."""
    raw_dir = tmp_path / STEP_DIRS["raw"]
    raw_dir.mkdir(parents=True)
    existing = raw_dir / f"1576-{SLUG}-2024-01-15.html"
    existing.write_text(
        "<!-- Saved from https://legislatie.just.ro/x at 2024-01-15T00:00:00Z "
        "using monolith v2.11.6 -->"
        "<html><head><meta charset='UTF-8'></head>"
        "<body><p>Lege</p></body></html>"
    )

    downloader = LegislatieJustDownloader(surse=[_source()], data_dir=tmp_path)
    monkeypatch.setattr(
        "scrapper_legislatie_just.downloader.find_monolith", lambda: "monolith"
    )
    fallback = LinkuriConsolidate(
        link_sursa="https://legislatie.just.ro/Public/DetaliiDocument/1576",
        data_consolidarii="2026-11-08",
        fara_data_consolidare=True,
    )
    monkeypatch.setattr(
        downloader, "preia_linkuri_consolidate", lambda url: [fallback]
    )
    monkeypatch.setattr(
        downloader, "preia_forma_printabila_link", lambda url: "https://x/print"
    )

    def fake_download(url, path):
        # Different monolith header/serialization, same law text.
        Path(path).write_text(
            "<!-- Saved from https://legislatie.just.ro/y at 2026-11-08T00:00:00Z "
            "using monolith v2.12.0 -->"
            "<html><head><meta charset='UTF-8'></head>"
            "<body><p>Lege</p></body></html>"
        )
        return True

    monkeypatch.setattr(downloader, "download_page", fake_download)

    result = downloader.download()

    assert result["downloaded"] == 0
    assert result["failed"] == 0
    assert sorted(path.name for path in raw_dir.glob("*.html")) == [existing.name]


def test_downloader_replaces_fallback_when_content_changed(tmp_path, monkeypatch):
    raw_dir = tmp_path / STEP_DIRS["raw"]
    raw_dir.mkdir(parents=True)
    existing = raw_dir / f"1576-{SLUG}-2024-01-15.html"
    existing.write_text("<html><body><p>Text vechi</p></body></html>")

    downloader = LegislatieJustDownloader(surse=[_source()], data_dir=tmp_path)
    monkeypatch.setattr(
        "scrapper_legislatie_just.downloader.find_monolith", lambda: "monolith"
    )
    fallback = LinkuriConsolidate(
        link_sursa="https://legislatie.just.ro/Public/DetaliiDocument/1576",
        data_consolidarii="2026-11-08",
        fara_data_consolidare=True,
    )
    monkeypatch.setattr(
        downloader, "preia_linkuri_consolidate", lambda url: [fallback]
    )
    monkeypatch.setattr(
        downloader, "preia_forma_printabila_link", lambda url: "https://x/print"
    )

    def fake_download(url, path):
        Path(path).write_text("<html><body><p>Text nou</p></body></html>")
        return True

    monkeypatch.setattr(downloader, "download_page", fake_download)

    result = downloader.download()

    assert result["downloaded"] == 1
    assert not existing.exists()
    assert [path.name for path in raw_dir.glob("*.html")] == [
        f"1576-{SLUG}-2026-11-08.html"
    ]


def test_downloader_prunes_older_versions_by_default(tmp_path, monkeypatch):
    """Default mode keeps only the latest consolidation for each source."""
    raw_dir = tmp_path / STEP_DIRS["raw"]
    clean_dir = tmp_path / STEP_DIRS["clean"]
    expanded_dir = tmp_path / STEP_DIRS["expanded"] / SLUG
    processed_dir = tmp_path / STEP_DIRS["processed"]
    for directory in (raw_dir, clean_dir, expanded_dir, processed_dir):
        directory.mkdir(parents=True)

    old_name = f"1576-{SLUG}-2024-01-15.html"
    for directory in (raw_dir, clean_dir, expanded_dir):
        (directory / old_name).write_text("<html><body><p>Text vechi</p></body></html>")
    (expanded_dir / f"1576-{SLUG}-2024-01-15.md").write_text("Text vechi")
    (processed_dir / "law.db").write_text("stale")

    downloader = LegislatieJustDownloader(surse=[_source()], data_dir=tmp_path)
    monkeypatch.setattr(
        "scrapper_legislatie_just.downloader.find_monolith", lambda: "monolith"
    )
    monkeypatch.setattr(
        downloader, "preia_linkuri_consolidate", lambda url: _consolidari()
    )
    monkeypatch.setattr(
        downloader, "preia_forma_printabila_link", lambda url: "https://x/print"
    )

    def fake_download(url, path):
        Path(path).write_text("<html><body><p>Text nou</p></body></html>")
        return True

    monkeypatch.setattr(downloader, "download_page", fake_download)

    result = downloader.download()

    assert result["downloaded"] == 1
    assert result["index_invalidated"] is True
    assert sorted(path.name for path in raw_dir.glob("*.html")) == [
        f"1577-{SLUG}-2024-06-20.html"
    ]
    assert not (clean_dir / old_name).exists()
    assert not (expanded_dir / old_name).exists()
    assert not (expanded_dir / f"1576-{SLUG}-2024-01-15.md").exists()
    assert not (processed_dir / "law.db").exists()


def test_downloader_keeps_all_versions_when_requested(tmp_path, monkeypatch):
    raw_dir = tmp_path / STEP_DIRS["raw"]
    raw_dir.mkdir(parents=True)
    old_name = f"1576-{SLUG}-2024-01-15.html"
    (raw_dir / old_name).write_text("<html><body><p>Text vechi</p></body></html>")

    downloader = LegislatieJustDownloader(
        surse=[_source()], data_dir=tmp_path, all_versions=True
    )
    monkeypatch.setattr(
        "scrapper_legislatie_just.downloader.find_monolith", lambda: "monolith"
    )
    monkeypatch.setattr(
        downloader, "preia_linkuri_consolidate", lambda url: _consolidari()
    )
    monkeypatch.setattr(
        downloader, "preia_forma_printabila_link", lambda url: "https://x/print"
    )

    def fake_download(url, path):
        Path(path).write_text("<html><body><p>Text nou</p></body></html>")
        return True

    monkeypatch.setattr(downloader, "download_page", fake_download)

    assert downloader.download()["downloaded"] == 1
    assert (raw_dir / old_name).exists()
    assert (raw_dir / f"1577-{SLUG}-2024-06-20.html").exists()


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


def test_downloader_removes_orphan_sources(tmp_path):
    raw_dir = tmp_path / STEP_DIRS["raw"]
    clean_dir = tmp_path / STEP_DIRS["clean"]
    expanded_dir = tmp_path / STEP_DIRS["expanded"]
    processed_dir = tmp_path / STEP_DIRS["processed"]
    for directory in (raw_dir, clean_dir, expanded_dir, processed_dir):
        directory.mkdir(parents=True, exist_ok=True)

    orphan_slug = "sursa-eliminata"
    keep_file = f"1576-{SLUG}-2024-01-15.html"
    orphan_file = f"9999-{orphan_slug}-2024-01-15.html"

    (raw_dir / keep_file).write_text("keep")
    (raw_dir / orphan_file).write_text("orphan")
    (clean_dir / keep_file).write_text("keep")
    (clean_dir / orphan_file).write_text("orphan")

    keep_dir = expanded_dir / SLUG
    orphan_dir = expanded_dir / orphan_slug
    keep_dir.mkdir()
    orphan_dir.mkdir()
    (keep_dir / keep_file).write_text("keep")
    (orphan_dir / f"toc-{orphan_slug}.json").write_text("{}")

    for name in ("law.db", "graph.db", "manifest.json", "catalog.json"):
        (processed_dir / name).write_text("stale")

    downloader = LegislatieJustDownloader(
        surse=[_source()], data_dir=tmp_path, known_surse=[_source()]
    )
    result = downloader.remove_orphan_sources()

    assert result == {"removed": 3, "index_invalidated": True}
    assert (raw_dir / keep_file).exists()
    assert (clean_dir / keep_file).exists()
    assert keep_dir.exists()
    assert not (raw_dir / orphan_file).exists()
    assert not (clean_dir / orphan_file).exists()
    assert not orphan_dir.exists()
    for name in ("law.db", "graph.db", "manifest.json", "catalog.json"):
        assert not (processed_dir / name).exists()


def test_downloader_invalidates_index_with_orphan_slug(tmp_path):
    processed_dir = tmp_path / STEP_DIRS["processed"]
    processed_dir.mkdir(parents=True)
    db_path = processed_dir / "law.db"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE docs(slug TEXT)")
    connection.execute("INSERT INTO docs(slug) VALUES('sursa-eliminata')")
    connection.commit()
    connection.close()

    downloader = LegislatieJustDownloader(
        surse=[_source()], data_dir=tmp_path, known_surse=[_source()]
    )
    result = downloader.remove_orphan_sources()

    assert result == {"removed": 0, "index_invalidated": True}
    assert not db_path.exists()


def test_downloader_keeps_configured_sources_outside_selection(tmp_path):
    other_source = next(
        s for s in surse_legislatie_just if s.slug == "codul-fiscal"
    )
    raw_dir = tmp_path / STEP_DIRS["raw"]
    raw_dir.mkdir(parents=True)
    other_file = f"{other_source.numar_link}-{other_source.slug}-2024-01-15.html"
    (raw_dir / other_file).write_text("keep")

    downloader = LegislatieJustDownloader(
        surse=[_source()],
        data_dir=tmp_path,
        known_surse=[_source(), other_source],
    )

    assert downloader.remove_orphan_sources() == {
        "removed": 0,
        "index_invalidated": False,
    }
    assert (raw_dir / other_file).exists()


def test_downloader_dry_run_keeps_orphan_sources(tmp_path):
    raw_dir = tmp_path / STEP_DIRS["raw"]
    raw_dir.mkdir(parents=True)
    orphan_file = raw_dir / "9999-sursa-eliminata-2024-01-15.html"
    orphan_file.write_text("orphan")

    downloader = LegislatieJustDownloader(
        surse=[_source()],
        data_dir=tmp_path,
        known_surse=[_source()],
        dry_run=True,
    )

    assert downloader.remove_orphan_sources() == {
        "removed": 0,
        "index_invalidated": False,
    }
    assert orphan_file.exists()


def test_download_removes_orphan_sources_before_downloading(tmp_path, monkeypatch):
    raw_dir = tmp_path / STEP_DIRS["raw"]
    raw_dir.mkdir(parents=True)
    orphan_file = raw_dir / "9999-sursa-eliminata-2024-01-15.html"
    orphan_file.write_text("orphan")

    downloader = LegislatieJustDownloader(
        surse=[_source()], data_dir=tmp_path, known_surse=[_source()]
    )
    monkeypatch.setattr(
        "scrapper_legislatie_just.downloader.find_monolith", lambda: "monolith"
    )
    monkeypatch.setattr(downloader, "preia_linkuri_consolidate", lambda url: [])

    result = downloader.download()

    assert result["orphans_removed"] == 1
    assert result["index_invalidated"] is False
    assert not orphan_file.exists()


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
