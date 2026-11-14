import re
import shutil
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .config import resolve_data_dir, step_dir
from .logger import log
from .misc import extract_file_meta, strip_diacritics
from .sources import Sursa, SursaConsolidata, SurseConsolidate, surse_legislatie_just


@dataclass
class LegislatieJustExpander:
    """Step 3: group cleaned documents, build a TOC and convert them to Markdown."""

    surse: list[Sursa] = field(default_factory=lambda: list(surse_legislatie_just))
    data_dir: str | Path | None = None
    clean_dir: str | Path | None = None
    expanded_dir: str | Path | None = None
    limit: int | None = None
    force: bool = False

    def __post_init__(self):
        root = resolve_data_dir(self.data_dir)
        self.clean_dir = (
            Path(self.clean_dir).expanduser().resolve()
            if self.clean_dir
            else step_dir(root, "clean")
        )
        self.expanded_dir = (
            Path(self.expanded_dir).expanduser().resolve()
            if self.expanded_dir
            else step_dir(root, "expanded")
        )
        self.clean_dir.mkdir(parents=True, exist_ok=True)
        self.expanded_dir.mkdir(parents=True, exist_ok=True)

    def extract_file_meta(self, filename: str) -> dict | None:
        return extract_file_meta(filename)

    def group_html_by_slug_in_dirs(self) -> int:
        log.info(
            f"Copiez fisierele curate in foldere pe surse: "
            f"{self.clean_dir} -> {self.expanded_dir}."
        )
        copied = 0
        skipped = 0

        for s in self.surse:
            slug_dir = self.expanded_dir / s.slug
            slug_dir.mkdir(parents=True, exist_ok=True)
            slug_copied = 0

            for html_path in self.clean_dir.glob("*.html"):
                meta = self.extract_file_meta(html_path.name)
                if not meta or s.slug != meta["slug"]:
                    continue

                if "T" in html_path.name:
                    new_filename = f"{html_path.name.split('T')[0]}.html"
                else:
                    new_filename = html_path.name

                dest_path = slug_dir / new_filename

                if self.force or not dest_path.exists():
                    log.debug(f"Copying {html_path.name} to {dest_path}")
                    shutil.copy2(html_path, dest_path)
                    copied += 1
                    slug_copied += 1
                else:
                    log.debug(f"Skipping {new_filename}, already exists in {s.slug}")
                    skipped += 1

            if slug_copied:
                log.info(f"[{s.slug}] Am copiat {slug_copied} fisiere.")

        log.info(
            f"Copiere incheiata: {copied} fisiere copiate, "
            f"{skipped} deja existente."
        )
        return copied

    def create_consolidated_json_toc(self) -> int:
        log.info("Generez fisierele TOC pentru fiecare sursa...")
        written = 0

        for s in self.surse:
            slug_dir = self.expanded_dir / s.slug

            surse_consolidate = []
            for html_path in slug_dir.glob(f"*{s.slug}*.html"):
                meta = self.extract_file_meta(html_path.name)

                if not meta:
                    log.warning(
                        f"Filename malformed: {html_path.name}. Expected something "
                        f"like: '291549-legea-contabilitatii-2024-12-05.html'"
                    )
                    continue

                sc = SursaConsolidata(
                    numar_link=meta["numar_link"],
                    html_name=html_path.name,
                    data_consolidarii=meta["data_consolidarii"],
                )
                surse_consolidate.append(sc)

            if surse_consolidate:
                surse_consolidate_sorted = sorted(
                    surse_consolidate,
                    key=lambda x: x.data_consolidarii or date.min,
                    reverse=True,
                )

                data = SurseConsolidate(
                    nume=s.nume,
                    descriere=s.descriere,
                    slug=s.slug,
                    consolidari=surse_consolidate_sorted,
                )

                json_data = data.model_dump_json(indent=4, ensure_ascii=False)
                toc_json_path = slug_dir / f"toc-{s.slug}.json"
                toc_json_path.write_text(json_data, encoding="utf-8")
                written += 1
                log.info(
                    f"[{s.slug}] TOC scris cu "
                    f"{len(surse_consolidate_sorted)} consolidari."
                )
            else:
                log.warning(f"[{s.slug}] Nu am gasit fisiere HTML pentru TOC.")

        log.info(f"TOC incheiat: {written} fisiere scrise.")
        return written

    def create_markdown_files(self) -> dict:
        try:
            from docling.datamodel.base_models import InputFormat
            from docling.document_converter import DocumentConverter
        except ImportError as ex:
            raise RuntimeError(
                "Docling is required for the expand step. Install it with "
                "'uv sync' (it is a project dependency)."
            ) from ex

        converter = DocumentConverter()
        converted = 0
        skipped = 0
        candidates = [
            html_path
            for s in self.surse
            for html_path in sorted(
                (self.expanded_dir / s.slug).glob(f"*{s.slug}*.html")
            )
        ]
        log.info(f"Convertesc {len(candidates)} fisiere HTML in Markdown...")

        for index, html_path in enumerate(candidates, start=1):
            if self.limit is not None and converted >= self.limit:
                log.info(f"Am atins limita de {self.limit} fisiere convertite.")
                break

            new_path = html_path.parent / f"{html_path.name.replace('.html', '.md')}"

            if not self.force and new_path.exists():
                log.info(
                    f"[{index}/{len(candidates)}] Deja convertit: "
                    f"{html_path.name}; sar peste."
                )
                skipped += 1
                continue

            log.info(
                f"[{index}/{len(candidates)}] Convertesc: {html_path.name}"
            )

            try:
                html_content = strip_diacritics(html_path.read_text(encoding="utf-8"))
                result = converter.convert_string(
                    html_content, format=InputFormat.HTML, name=html_path.name
                )
                content = result.document.export_to_markdown()
                new_path.write_text(content.strip(), encoding="utf-8")
                converted += 1
            except Exception as ex:
                log.exception(f"Nu pot converti {html_path}: {ex}")

        log.info(
            f"Conversie incheiata: {converted} fisiere Markdown, "
            f"{skipped} deja existente."
        )
        return {"converted": converted, "skipped": skipped}

    def expand(self) -> dict:
        copied = self.group_html_by_slug_in_dirs()
        tocs = self.create_consolidated_json_toc()
        markdown = self.create_markdown_files()
        return {"copied": copied, "tocs": tocs, **markdown}
