import random
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_random_exponential

from .browser_headers import get_random_headers
from .config import resolve_data_dir, step_dir
from .logger import log
from .misc import extract_file_meta, jitter_call
from .sources import Sursa


class LinkuriConsolidate(BaseModel):
    link_sursa: str
    data_consolidarii: str


class MonolithNotFoundError(RuntimeError):
    """Raised when the required ``monolith`` executable is missing."""


def find_monolith() -> str:
    """Return the ``monolith`` executable path or fail with a clear error."""
    monolith = shutil.which("monolith")
    if not monolith:
        raise MonolithNotFoundError(
            "monolith is required but was not found on PATH. Install it from "
            "https://github.com/Y2Z/monolith#installation and run "
            "'monolith --version' to verify."
        )
    return monolith


@dataclass
class LegislatieJustDownloader:
    """Step 1: download printable HTML versions from legislatie.just.ro."""

    surse: list[Sursa]
    data_dir: str | Path | None = None
    output_dir: str | Path | None = None
    limit: int | None = None
    force: bool = False
    dry_run: bool = False
    all_versions: bool = False
    # Full configured source list, used to detect sources that were removed
    # from sources.toml. It defaults to ``surse`` so programmatic callers that
    # only pass a subset never delete unrelated files.
    known_surse: list[Sursa] | None = None

    def __post_init__(self):
        self.root = resolve_data_dir(self.data_dir)
        self.output_dir = (
            Path(self.output_dir).expanduser().resolve()
            if self.output_dir
            else step_dir(self.root, "raw")
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.clean_dir = step_dir(self.root, "clean")
        self.expanded_dir = step_dir(self.root, "expanded")
        self.processed_dir = step_dir(self.root, "processed")
        self.known_surse = (
            list(self.known_surse) if self.known_surse is not None else list(self.surse)
        )

    def get_user_agent(self) -> str:
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:123.0) Gecko/20100101 Firefox/123.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3.1 Safari/605.1.15",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
            "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
        ]

        # 30% chance to generate a completely randomized Firefox string.
        if random.random() < 0.30:
            major_ver = random.randint(110, 125)
            minor_ver = random.randint(0, 9)
            build_date = random.randint(20100101, 20240101)
            return (
                f"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:{major_ver}.{minor_ver}) "
                f"Gecko/{build_date} Firefox/{major_ver}.0"
            )

        return random.choice(user_agents)

    @retry(
        wait=wait_random_exponential(multiplier=5, min=4, max=20),
        stop=stop_after_attempt(10),
    )
    @jitter_call(3, 6)
    def get_bs_soup(self, url: str) -> BeautifulSoup:
        response = requests.get(
            url=url, headers=get_random_headers(), timeout=60, allow_redirects=False
        )
        response.raise_for_status()

        response.encoding = response.apparent_encoding
        return BeautifulSoup(response.text, "html5lib")

    def creeaza_link_complet(self, relative_url: str) -> str:
        clean_path = relative_url.replace("~/../../../", "")
        return urljoin("https://legislatie.just.ro", clean_path)

    def preia_linkuri_consolidate(self, main_url: str) -> list[LinkuriConsolidate]:
        default_result = [
            LinkuriConsolidate(
                link_sursa=main_url, data_consolidarii=date.today().isoformat()
            )
        ]

        soup = self.get_bs_soup(main_url)

        historic_div = soup.find("div", id="istoric_fa")
        if not historic_div:
            log.info(
                f"Nu exista istoric de consolidari pentru {main_url}; "
                "folosesc forma curenta."
            )
            return default_result

        all_a_tags = historic_div.find_all("a")
        if not all_a_tags:
            log.info(
                f"Nu exista istoric de consolidari pentru {main_url}; "
                "folosesc forma curenta."
            )
            return default_result

        results = []
        for link_tag in all_a_tags:
            date_text = link_tag.get_text(strip=True)

            if link_tag.has_attr("href"):
                absolute_url = self.creeaza_link_complet(link_tag["href"])
            else:
                absolute_url = main_url

            try:
                date_obj = datetime.strptime(date_text, "%d.%m.%Y")

                # Ignoram link-urile mai vechi de 5 ani.
                five_years_ago = datetime.now() - timedelta(days=5 * 365)
                if date_obj < five_years_ago:
                    continue
            except ValueError:
                continue

            results.append(
                LinkuriConsolidate(
                    link_sursa=absolute_url,
                    data_consolidarii=date_obj.date().isoformat(),
                )
            )

        return results or default_result

    def preia_cel_mai_recent_link(
        self, main_url: str
    ) -> LinkuriConsolidate | None:
        results = self.preia_linkuri_consolidate(main_url)
        if not results:
            return None
        return max(results, key=lambda x: date.fromisoformat(x.data_consolidarii))

    def preia_forma_printabila_link(self, url: str) -> str:
        soup = self.get_bs_soup(url)

        css_selector1 = 'a.btn-primary:-soup-contains("Forma printabilă")'
        css_selector2 = 'a[href^="/Public/FormaPrintabila"]'

        printable_link_tag = soup.select_one(css_selector1)
        if not printable_link_tag:
            log.debug(
                f"Nu am putut gasi forma printabila cu: {css_selector1}. Url: {url}"
            )
            links = [
                a
                for a in soup.select(css_selector2)
                if "Forma printabilă" in a.get_text()
            ]
            if not links:
                log.debug(
                    f"Nu am putut gasi forma printabila cu: {css_selector2}. Url: {url}"
                )
                raise RuntimeError(f"Nu am putut prelua forma printabila din url: {url}")

            printable_link_tag = links[0]

        relative_path = printable_link_tag["href"]
        full_url = urljoin("https://legislatie.just.ro", relative_path)

        log.debug(f"Am gasit link forma printabila: {full_url}")
        return full_url

    @retry(
        wait=wait_random_exponential(multiplier=5, min=4, max=20),
        stop=stop_after_attempt(2),
    )
    @jitter_call(4, 8)
    def download_page(
        self, link_forma_printabila: str, output_html_path: str | Path
    ) -> bool:
        output_html_path = Path(output_html_path)
        output_html_path.parent.mkdir(parents=True, exist_ok=True)

        return self._download_with_monolith(link_forma_printabila, output_html_path)

    def _download_with_monolith(self, url: str, output_html_path: Path) -> bool:
        monolith = find_monolith()

        cmd_list = [
            monolith,
            "-t",
            "120",  # Network timeout
            "-j",  # No JavaScript
            "-I",  # Isolate (no external requests)
            "-u",
            self.get_user_agent(),
            url,
            "-o",
            str(output_html_path),
        ]

        try:
            subprocess.run(cmd_list, check=True, capture_output=True, text=True)
            log.success(f"Salvat: {output_html_path} (din {url})")
            return True
        except subprocess.CalledProcessError as e:
            log.error(f"Command failed with exit code {e.returncode}")
            if e.stderr:
                log.error(f"Error details: {e.stderr.strip()}")
            if e.stdout:
                log.debug(f"Console output: {e.stdout.strip()}")
            raise RuntimeError(f"monolith failed for {url}") from e

    def _existing_versions(self) -> dict[str, set[str]]:
        """Map each slug to the consolidation dates already saved in ``raw``."""
        versions: dict[str, set[str]] = {}
        for path in self.output_dir.glob("*.html"):
            meta = extract_file_meta(path.name)
            if not meta:
                continue
            versions.setdefault(meta["slug"], set()).add(meta["data_consolidarii"])
        return versions

    @staticmethod
    def _format_dates(dates: set[str] | list[str]) -> str:
        """Render saved dates newest-first, truncating long lists."""
        ordered = sorted(dates, reverse=True)
        if not ordered:
            return "niciuna"
        shown = ", ".join(ordered[:3])
        if len(ordered) > 3:
            shown += f" (+{len(ordered) - 3})"
        return shown

    def find_orphan_sources(self) -> dict[str, list[Path]]:
        """Return artifacts that belong to slugs no longer in the source list.

        Sources removed from ``sources.toml`` leave files behind in every
        pipeline step. The download step is the entry point, so it cleans them
        before doing any network work.
        """
        known_slugs = {s.slug for s in self.known_surse}
        orphans: dict[str, list[Path]] = {"raw": [], "clean": [], "expanded": []}
        if not known_slugs:
            return orphans

        for step_name, directory in (
            ("raw", self.output_dir),
            ("clean", self.clean_dir),
        ):
            for path in sorted(directory.glob("*.html")):
                meta = extract_file_meta(path.name)
                if meta and meta["slug"] not in known_slugs:
                    orphans[step_name].append(path)

        for path in sorted(self.expanded_dir.iterdir()):
            if not path.is_dir() or path.name.startswith("."):
                continue
            if path.name in known_slugs:
                continue
            # Only touch directories that look like an expanded source.
            looks_like_source = (path / f"toc-{path.name}.json").exists() or any(
                path.glob("*.html")
            )
            if looks_like_source:
                orphans["expanded"].append(path)

        return orphans

    def _index_has_orphans(self, known_slugs: set[str]) -> bool:
        """Check whether the processed index still contains removed slugs."""
        db_path = self.processed_dir / "law.db"
        if not known_slugs or not db_path.exists():
            return False
        try:
            connection = sqlite3.connect(
                f"{db_path.resolve().as_uri()}?mode=ro", uri=True
            )
            try:
                rows = connection.execute("SELECT DISTINCT slug FROM docs").fetchall()
            finally:
                connection.close()
        except sqlite3.Error:
            return False
        return any(row[0] not in known_slugs for row in rows)

    def remove_orphan_sources(self) -> dict:
        """Delete files and folders for sources removed from ``sources.toml``.

        When an expanded source is removed, the processed index is invalidated
        so it cannot keep answering from stale data. The next ``process`` run
        rebuilds it from the remaining sources.
        """
        known_slugs = {s.slug for s in self.known_surse}
        orphans = self.find_orphan_sources()
        removed = 0

        for step_name, paths in orphans.items():
            for path in paths:
                if self.dry_run:
                    log.info(
                        f"[dry-run] As sterge sursa eliminata ({step_name}): {path}"
                    )
                    continue
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                removed += 1
                log.warning(f"Am sters sursa eliminata ({step_name}): {path}")

        index_invalidated = False
        if not self.dry_run and (
            orphans["expanded"] or self._index_has_orphans(known_slugs)
        ):
            for name in (
                "law.db",
                "law.db.tmp",
                "graph.db",
                "manifest.json",
                "catalog.json",
            ):
                artifact = self.processed_dir / name
                if artifact.exists():
                    artifact.unlink()
                    index_invalidated = True
            if index_invalidated:
                log.warning(
                    "Indexul procesat contine inca surse eliminate; ruleaza 'process' "
                    "pentru a-l reconstrui."
                )

        return {"removed": removed, "index_invalidated": index_invalidated}

    def download(self) -> dict:
        # Local cleanup runs first so removed sources never survive a failed
        # download caused by a missing tool or a network error.
        cleanup = self.remove_orphan_sources()

        # Fail fast before any network calls when the required tool is missing.
        if not self.dry_run:
            find_monolith()

        failed_downloads: set[str] = set()
        cache_linkuri_consolidate: dict[str, list[LinkuriConsolidate]] = {}
        existing_versions = self._existing_versions()
        downloaded = 0
        skipped = 0
        new_versions = 0

        mode = "dry-run (doar descoperire)" if self.dry_run else "descarcare"
        log.info(
            f"Pornesc {mode} pentru {len(self.surse)} surse. "
            f"Limita: {self.limit if self.limit is not None else 'fara'}, "
            f"toate versiunile: {'da' if self.all_versions else 'nu'}, "
            f"forteaza: {'da' if self.force else 'nu'}."
        )
        log.info(f"Fisierele HTML sunt salvate in: {self.output_dir}")

        for sursa in self.surse:
            if self.limit is not None and downloaded >= self.limit:
                log.info(
                    f"Am atins limita de {self.limit} descarcari; "
                    "opresc descoperirea de surse."
                )
                break

            linkuri_consolidate = cache_linkuri_consolidate.get(sursa.slug)

            if linkuri_consolidate is None:
                log.info(f"[{sursa.slug}] Verific consolidarile la {sursa.link}")
                try:
                    if self.all_versions:
                        linkuri_consolidate = self.preia_linkuri_consolidate(
                            sursa.link
                        )
                    else:
                        latest = self.preia_cel_mai_recent_link(sursa.link)
                        linkuri_consolidate = [latest] if latest else []
                except Exception as ex:
                    log.error(f"[{sursa.slug}] Nu pot prelua consolidari: {ex}")
                    failed_downloads.add(sursa.link or sursa.slug)
                    continue
                cache_linkuri_consolidate[sursa.slug] = linkuri_consolidate

            if not linkuri_consolidate:
                log.warning(
                    f"[{sursa.slug}] Nu am gasit nicio consolidare; sar peste sursa."
                )
                continue

            local_dates = existing_versions.get(sursa.slug, set())
            if self.all_versions:
                log.info(
                    f"[{sursa.slug}] Am gasit {len(linkuri_consolidate)} versiuni. "
                    f"Local: {self._format_dates(local_dates)}."
                )
            else:
                log.info(
                    f"[{sursa.slug}] Cea mai recenta consolidare: "
                    f"{linkuri_consolidate[0].data_consolidarii}. "
                    f"Local: {self._format_dates(local_dates)}."
                )

            source_downloaded = 0
            source_new = 0
            source_skipped = 0

            for link_consolidat in linkuri_consolidate:
                if self.limit is not None and downloaded >= self.limit:
                    break

                data_consolidarii = link_consolidat.data_consolidarii
                numar_link = link_consolidat.link_sursa.rsplit("/", 1)[-1]
                filename = f"{numar_link}-{sursa.slug}-{data_consolidarii}.html"
                output_html_path = self.output_dir / filename

                if not self.force and output_html_path.exists():
                    log.info(
                        f"[{sursa.slug}] Deja salvat: versiunea {data_consolidarii} "
                        f"({filename}); sar peste."
                    )
                    skipped += 1
                    source_skipped += 1
                    continue

                is_new_version = (
                    bool(local_dates) and data_consolidarii not in local_dates
                )
                if is_new_version:
                    new_versions += 1
                    source_new += 1
                    log.info(
                        f"[{sursa.slug}] Versiune noua: {data_consolidarii} "
                        f"(local: {self._format_dates(local_dates)}). Descarc..."
                    )
                elif self.force and output_html_path.exists():
                    log.info(
                        f"[{sursa.slug}] Re-descarc versiunea {data_consolidarii} "
                        f"pentru ca ai folosit --force."
                    )
                elif not local_dates:
                    log.info(
                        f"[{sursa.slug}] Nu exista date locale. "
                        f"Descarc versiunea {data_consolidarii}..."
                    )
                else:
                    log.info(
                        f"[{sursa.slug}] Descarc versiunea {data_consolidarii} "
                        f"(local: {self._format_dates(local_dates)})."
                    )

                if self.dry_run:
                    log.info(
                        f"[dry-run] [{sursa.slug}] As descarca versiunea "
                        f"{data_consolidarii} din {link_consolidat.link_sursa}"
                    )
                    downloaded += 1
                    source_downloaded += 1
                    continue

                log.debug(
                    f"[{sursa.slug}] Preluam forma printabila din: "
                    f"{link_consolidat.link_sursa}"
                )

                try:
                    link_forma_printabila = self.preia_forma_printabila_link(
                        link_consolidat.link_sursa
                    )
                except Exception as ex:
                    log.error(
                        f"[{sursa.slug}] Nu pot prelua forma printabila pentru "
                        f"versiunea {data_consolidarii}: {ex}"
                    )
                    failed_downloads.add(link_consolidat.link_sursa)
                    continue

                log.info(
                    f"[{sursa.slug}] Descarc versiunea {data_consolidarii}: "
                    f"{link_forma_printabila}"
                )

                try:
                    if self.download_page(link_forma_printabila, output_html_path):
                        downloaded += 1
                        source_downloaded += 1
                        existing_versions.setdefault(sursa.slug, set()).add(
                            data_consolidarii
                        )
                        local_dates = existing_versions[sursa.slug]
                    else:
                        failed_downloads.add(link_forma_printabila)
                except Exception as ex:
                    log.error(
                        f"[{sursa.slug}] Descarcarea versiunii {data_consolidarii} "
                        f"a esuat ({link_forma_printabila}): {ex}"
                    )
                    failed_downloads.add(link_forma_printabila)

            if source_downloaded == 0 and source_skipped:
                log.info(
                    f"[{sursa.slug}] Nimic nou: toate cele {source_skipped} "
                    "versiuni sunt deja salvate."
                )
            elif source_downloaded:
                log.info(
                    f"[{sursa.slug}] Gata: {source_downloaded} descarcate, "
                    f"{source_new} versiuni noi, {source_skipped} deja existente."
                )

        if failed_downloads:
            log.warning(
                f"Avem {len(failed_downloads)} descarcari nereusite... "
                "Vom incerca mai tarziu."
            )

        log.info(
            f"Descarcare incheiata: {downloaded} salvate, {skipped} deja existente, "
            f"{new_versions} versiuni noi, {len(failed_downloads)} esuate."
        )

        return {
            "downloaded": downloaded,
            "failed": len(failed_downloads),
            "failed_urls": sorted(failed_downloads),
            "orphans_removed": cleanup["removed"],
            "index_invalidated": cleanup["index_invalidated"],
        }
