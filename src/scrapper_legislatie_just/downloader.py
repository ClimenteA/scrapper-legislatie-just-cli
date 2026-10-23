import random
import shutil
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
from .misc import jitter_call
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

    def __post_init__(self):
        root = resolve_data_dir(self.data_dir)
        self.output_dir = (
            Path(self.output_dir).expanduser().resolve()
            if self.output_dir
            else step_dir(root, "raw")
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)

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
            log.info("Forma de baza este cea curenta.")
            return default_result

        all_a_tags = historic_div.find_all("a")
        if not all_a_tags:
            log.info("Forma de baza este cea curenta.")
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
            log.success(f"Am salvat html din linkul {url}")
            return True
        except subprocess.CalledProcessError as e:
            log.error(f"Command failed with exit code {e.returncode}")
            if e.stderr:
                log.error(f"Error details: {e.stderr.strip()}")
            if e.stdout:
                log.debug(f"Console output: {e.stdout.strip()}")
            raise RuntimeError(f"monolith failed for {url}") from e

    def download(self) -> dict:
        # Fail fast before any network calls when the required tool is missing.
        if not self.dry_run:
            find_monolith()

        failed_downloads: set[str] = set()
        cache_linkuri_consolidate: dict[str, list[LinkuriConsolidate]] = {}
        downloaded = 0

        for sursa in self.surse:
            if self.limit is not None and downloaded >= self.limit:
                break

            linkuri_consolidate = cache_linkuri_consolidate.get(sursa.slug)

            if linkuri_consolidate is None:
                try:
                    if self.all_versions:
                        linkuri_consolidate = self.preia_linkuri_consolidate(
                            sursa.link
                        )
                    else:
                        latest = self.preia_cel_mai_recent_link(sursa.link)
                        linkuri_consolidate = [latest] if latest else []
                except Exception as ex:
                    log.error(f"Nu pot prelua consolidari pentru {sursa.slug}: {ex}")
                    failed_downloads.add(sursa.link or sursa.slug)
                    continue
                cache_linkuri_consolidate[sursa.slug] = linkuri_consolidate

            log.debug(
                f"{sursa.slug}: am gasit {len(linkuri_consolidate)} linkuri consolidate"
            )

            for link_consolidat in linkuri_consolidate:
                if self.limit is not None and downloaded >= self.limit:
                    break

                numar_link = link_consolidat.link_sursa.rsplit("/", 1)[-1]
                filename = (
                    f"{numar_link}-{sursa.slug}-"
                    f"{link_consolidat.data_consolidarii}.html"
                )
                output_html_path = self.output_dir / filename

                if not self.force and output_html_path.exists():
                    log.debug(f"Skip. Avem deja {filename}")
                    continue

                if self.dry_run:
                    log.info(
                        f"[dry-run] {link_consolidat.data_consolidarii} "
                        f"{sursa.slug}: {link_consolidat.link_sursa}"
                    )
                    downloaded += 1
                    continue

                log.debug(f"Preluam date din linkul: {link_consolidat.link_sursa}")

                try:
                    link_forma_printabila = self.preia_forma_printabila_link(
                        link_consolidat.link_sursa
                    )
                except Exception as ex:
                    log.error(f"Nu pot prelua forma printabila: {ex}")
                    failed_downloads.add(link_consolidat.link_sursa)
                    continue

                try:
                    if self.download_page(link_forma_printabila, output_html_path):
                        downloaded += 1
                    else:
                        failed_downloads.add(link_forma_printabila)
                except Exception as ex:
                    log.error(f"Download failed for {link_forma_printabila}: {ex}")
                    failed_downloads.add(link_forma_printabila)

        if failed_downloads:
            log.warning(
                f"Avem {len(failed_downloads)} descarcari nereusite... "
                "Vom incerca mai tarziu."
            )

        return {
            "downloaded": downloaded,
            "failed": len(failed_downloads),
            "failed_urls": sorted(failed_downloads),
        }
