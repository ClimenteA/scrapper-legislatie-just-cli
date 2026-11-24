"""Step 3: turn the cleaned HTML and the source PDFs into Markdown.

``3-expanded`` holds **only** Markdown, one file per act/consolidation:

* cleaned HTML -> Markdown with :mod:`markdownify` (headings, lists, tables);
* source PDFs (``5-pdfs``) -> Markdown with LiteParse + the ``ocria-nano``
  OCR model (see :mod:`pdf_convert`).

Text is stored without Romanian diacritics, exactly like the cleaned HTML, so
the BM25 index and the user queries speak the same alphabet.

Each slug directory also keeps ``toc-<slug>.json`` with the act metadata and
the list of consolidations. The file name is the identity of a version:
``<numar_link>-<slug>-<YYYY-MM-DD>.md``.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup
from markdownify import markdownify

from .config import resolve_data_dir, step_dir
from .logger import log
from .misc import extract_file_meta, strip_diacritics
from .pdf_convert import ensure_ocr_model, pdf_to_markdown
from .pdf_ingest import (
    load_declaration_manifest,
    pdf_descriptors,
    relative_to_root,
    resolve_pdf_sources_dir,
    sync_pdf_into_data_dir,
)
from .sources import Sursa, surse_legislatie_just

BLANK_LINES_RE = re.compile(r"\n{3,}")
DROP_SELECTORS = ("script", "style", "noscript", "svg", ".toc-container")


@dataclass
class LegislatieJustExpander:
    """Step 3: build the Markdown corpus (HTML + PDF) and the TOC files."""

    surse: list[Sursa] = field(default_factory=lambda: list(surse_legislatie_just))
    data_dir: str | Path | None = None
    clean_dir: str | Path | None = None
    expanded_dir: str | Path | None = None
    pdf_sources_dir: str | Path | None = None
    limit: int | None = None
    force: bool = False
    pdf_sources: list[dict] | None = None

    def __post_init__(self) -> None:
        self.root = resolve_data_dir(self.data_dir)
        self.clean_dir = (
            Path(self.clean_dir).expanduser().resolve()
            if self.clean_dir
            else step_dir(self.root, "clean")
        )
        self.expanded_dir = (
            Path(self.expanded_dir).expanduser().resolve()
            if self.expanded_dir
            else step_dir(self.root, "expanded")
        )
        self.clean_dir.mkdir(parents=True, exist_ok=True)
        self.expanded_dir.mkdir(parents=True, exist_ok=True)
        if self.pdf_sources is None:
            self.pdf_sources = pdf_descriptors(self.root, self.pdf_sources_dir)

    def extract_file_meta(self, filename: str) -> dict | None:
        return extract_file_meta(filename)

    # ------------------------------------------------------------------ HTML
    @staticmethod
    def html_to_markdown(html: str) -> str:
        """Convert one cleaned HTML document to Markdown.

        Links are stripped (``strip=["a"]``): the new corpus identifies a
        source by act name + slug + version, not by a fragile HTML anchor.
        """
        soup = BeautifulSoup(strip_diacritics(html), "lxml")
        for selector in DROP_SELECTORS:
            for element in soup.select(selector):
                element.decompose()
        body = soup.body or soup
        text = markdownify(
            str(body),
            heading_style="ATX",
            bullets="-",
            strip=["a"],
        )
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        return BLANK_LINES_RE.sub("\n\n", text).strip() + "\n"

    def convert_clean_html(self) -> dict:
        """Write ``3-expanded/<slug>/<numar>-<slug>-<date>.md`` for each act."""
        selected = {s.slug for s in self.surse}
        converted = 0
        skipped = 0
        candidates = [
            path
            for path in sorted(self.clean_dir.glob("*.html"))
            if (meta := extract_file_meta(path.name)) and meta["slug"] in selected
        ]
        log.info(f"Convertesc {len(candidates)} fisiere HTML in Markdown...")

        for index, html_path in enumerate(candidates, start=1):
            meta = extract_file_meta(html_path.name)
            if not meta:
                continue
            slug_dir = self.expanded_dir / meta["slug"]
            slug_dir.mkdir(parents=True, exist_ok=True)
            md_path = slug_dir / f"{html_path.stem}.md"

            if not self.force and md_path.exists():
                skipped += 1
                continue
            if self.limit is not None and converted >= self.limit:
                log.info(f"Am atins limita de {self.limit} fisiere convertite.")
                break

            log.info(f"[{index}/{len(candidates)}] Convertesc: {html_path.name}")
            try:
                md_path.write_text(
                    self.html_to_markdown(html_path.read_text(encoding="utf-8")),
                    encoding="utf-8",
                )
                converted += 1
            except Exception as ex:  # noqa: BLE001 - one bad file must not stop the run
                log.exception(f"Nu pot converti {html_path}: {ex}")

        log.info(
            f"Conversie HTML incheiata: {converted} Markdown, "
            f"{skipped} deja existente."
        )
        return {"converted": converted, "skipped": skipped}

    # ------------------------------------------------------------------- PDF
    def _pdf_markdown_path(self, descriptor: dict) -> Path:
        return self.expanded_dir / descriptor["slug"] / descriptor["md_name"]

    def convert_pdfs(self) -> dict:
        """Serve every source PDF under ``5-pdfs`` and convert it to Markdown."""
        descriptors = list(self.pdf_sources or [])
        if not descriptors:
            return {"converted": 0, "skipped": 0, "removed": 0}

        ensure_ocr_model(self.root)
        converted = 0
        skipped = 0

        for index, descriptor in enumerate(descriptors, start=1):
            source = Path(descriptor["source_path"])
            if not source.is_file():
                log.warning(f"PDF lipsa: {source}")
                continue
            try:
                sync_pdf_into_data_dir(source, self.root, descriptor["slug"])
            except OSError as ex:
                log.error(f"Nu pot publica {source} in 5-pdfs: {ex}")

            md_path = self._pdf_markdown_path(descriptor)
            if not self.force and md_path.exists():
                skipped += 1
                continue
            if self.limit is not None and converted >= self.limit:
                log.info(f"Am atins limita de {self.limit} PDF-uri convertite.")
                break

            log.info(f"[{index}/{len(descriptors)}] Convertesc PDF: {source.name}")
            try:
                md_path.parent.mkdir(parents=True, exist_ok=True)
                md_path.write_text(pdf_to_markdown(source), encoding="utf-8")
                converted += 1
            except Exception as ex:  # noqa: BLE001 - one bad PDF must not stop the run
                log.exception(f"Nu pot converti {source}: {ex}")

        log.info(
            f"Conversie PDF incheiata: {converted} Markdown, "
            f"{skipped} deja existente."
        )
        return {"converted": converted, "skipped": skipped, "removed": 0}

    # ------------------------------------------------------------------- TOC
    def _write_toc(self, slug: str, nume: str, descriere: str, items: list[dict]) -> bool:
        if not items:
            return False
        items.sort(key=lambda item: item.get("data_consolidarii") or "", reverse=True)
        payload = {
            "nume": nume,
            "descriere": descriere,
            "slug": slug,
            "consolidari": items,
        }
        import json

        (self.expanded_dir / slug / f"toc-{slug}.json").write_text(
            json.dumps(payload, indent=4, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return True

    def create_consolidated_json_toc(self) -> int:
        log.info("Generez fisierele TOC pentru fiecare sursa...")
        written = 0

        for source in self.surse:
            slug_dir = self.expanded_dir / source.slug
            items: list[dict] = []
            for md_path in sorted(slug_dir.glob(f"*{source.slug}*.md")):
                meta = extract_file_meta(md_path.name)
                if not meta:
                    log.warning(
                        f"Nume de fisier neasteptat: {md_path.name}. "
                        "Format asteptat: '<numar>-<slug>-<YYYY-MM-DD>.md'"
                    )
                    continue
                items.append(
                    {
                        "numar_link": int(meta["numar_link"]),
                        "md_name": md_path.name,
                        "data_consolidarii": meta["data_consolidarii"],
                        "link": f"https://legislatie.just.ro/Public/DetaliiDocument/"
                        f"{meta['numar_link']}",
                    }
                )
            if self._write_toc(source.slug, source.nume, source.descriere, items):
                written += 1
                log.info(f"[{source.slug}] TOC scris cu {len(items)} consolidari.")
            else:
                log.warning(f"[{source.slug}] Nu am gasit fisiere Markdown pentru TOC.")

        for descriptor in self.pdf_sources or []:
            md_path = self._pdf_markdown_path(descriptor)
            if not md_path.exists():
                continue
            items = [
                {
                    "numar_link": descriptor["numar_link"],
                    "md_name": md_path.name,
                    "data_consolidarii": descriptor["date"],
                    "link": descriptor.get("source_url"),
                    "source_pdf": relative_to_root(
                        self.root
                        / "5-pdfs"
                        / descriptor["slug"]
                        / Path(descriptor["source_path"]).name,
                        self.root,
                    ),
                }
            ]
            if self._write_toc(
                descriptor["slug"],
                descriptor["title"],
                descriptor["description"],
                items,
            ):
                written += 1

        log.info(f"TOC incheiat: {written} fisiere scrise.")
        return written

    # ------------------------------------------------------------------ entry
    def expand(self) -> dict:
        html_result = self.convert_clean_html()
        pdf_result = self.convert_pdfs()
        tocs = self.create_consolidated_json_toc()
        return {
            "converted": html_result["converted"] + pdf_result["converted"],
            "skipped": html_result["skipped"] + pdf_result["skipped"],
            "html_converted": html_result["converted"],
            "pdf_converted": pdf_result["converted"],
            "tocs": tocs,
        }


def pdf_sources_as_surse(data_root: str | Path, sources_dir: str | Path | None = None) -> list[Sursa]:
    """PDF descriptors as :class:`Sursa` objects (for orphan detection only)."""
    return [
        Sursa(
            nume=descriptor["title"],
            descriere=descriptor["description"],
            numar_link=descriptor["numar_link"],
            slug=descriptor["slug"],
        )
        for descriptor in pdf_descriptors(data_root, sources_dir)
    ]


# Backwards-compatible helper: callers that only need the list of descriptors.
def discover_pdf_sources(
    data_root: str | Path, sources_dir: str | Path | None = None
) -> list[dict]:
    return pdf_descriptors(data_root, sources_dir)
