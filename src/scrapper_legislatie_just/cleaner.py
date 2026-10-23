import hashlib
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import markdown
from bs4 import BeautifulSoup, NavigableString, Tag
from unidecode import unidecode

from .config import resolve_data_dir, step_dir
from .logger import log
from .misc import extract_file_meta
from .ocr import extract_table_from_image_base64
from .sources import Sursa, surse_legislatie_just

# ── Links & style — safe to parse with BS4 (no JS) ───────────
HEAD_LINKS = """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Merriweather:ital,wght@0,300;0,400;0,700;1,400&family=Roboto:wght@400;700&display=swap" rel="stylesheet">
"""


# ── Scroll-to-top JS — raw string ─────────────────────────────
SCROLL_JS = r"""
    document.addEventListener("DOMContentLoaded", () => {
        const btn = document.getElementById("scrollTopBtn");
        window.onscroll = function () {
            if (!btn) return;
            if (document.body.scrollTop > 300 || document.documentElement.scrollTop > 300) {
                btn.style.display = "block";
            } else {
                btn.style.display = "none";
            }
        };
    });
 
    function topFunction() {
        window.scrollTo({ top: 0 });
    }
"""

# ── CSS — safe to keep as a normal string (no JS regex in it) ─
STYLES_CSS = """
   /* 1. SETĂRI DE BAZĂ */
    html, body {
        margin: 0;
        padding: 0;
        width: 100%;
        height: auto !important;
        background-color: #f4f6f8;
        overflow-x: hidden;
    }

    body {
        font-family: 'Merriweather', serif;
        font-size: 11pt;
        line-height: 1.7;
        color: #1a1a1a;
        width: 90%;
        max-width: 1600px;
        margin: 0 auto;
        background: #ffffff;
        padding: 60px 80px;
        box-shadow: 0 4px 20px rgba(0,0,0,0.08);
        border-radius: 2px;
        box-sizing: border-box;
        word-wrap: break-word;
        margin-bottom: 100px !important;
    }

    /* OCR TABLES */
    .ocr-text-wrapper table {
        width: 100%;
        border-collapse: collapse;
        font-family: monospace;
        font-size: 12px;
    }
    .ocr-text-wrapper td, th {
        border: 1px solid #ccc;
        padding: 8px;
        min-width: 120px;
    }
    
    /* SCROLL TO TOP */

    .size-6 {
        height: 20px;
        width: 20px;
    }

    #scrollTopBtn {
        display: none;
        position: fixed;
        bottom: 30px;
        right: 30px;
        z-index: 9999;
        border: none;
        outline: none;
        background-color: #2c3e50;
        color: white;
        cursor: pointer;
        padding: 15px;
        border-radius: 50%;
        width: 50px;
        height: 50px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.2);
        transition: all 0.3s ease;
        font-size: 20px;
        line-height: 1;
    }

    #scrollTopBtn:hover {
        background-color: #3498db;
        transform: translateY(-3px);
        box-shadow: 0 6px 15px rgba(0,0,0,0.3);
    }

    .S_PRE {
        white-space:pre;
    }

    .S_ALN {
        display: block;
        margin-bottom: 0.5rem;
    }

    /* 3. STRUCTURA MARE (TITLURI ȘI PARȚI) */
    .S_TTL_TTL {
        display: block;
        font-family: 'Roboto', sans-serif;
        text-align: center;
        font-weight: 700;
        font-size: 1.2rem;
        color: #95a5a6;
        margin-top: 60px;
        margin-bottom: 10px;
        text-transform: uppercase;
        letter-spacing: 4px;
    }

    .S_TTL_DEN {
        display: block;
        font-family: 'Roboto', sans-serif;
        text-align: center;
        font-weight: 700;
        font-size: 1.8rem;
        color: #2c3e50;
        margin-bottom: 50px;
        padding: 20px 0;
        line-height: 1.2;
        text-transform: uppercase;
        border-top: 3px double #dcdde1;
        border-bottom: 3px double #dcdde1;
        background-color: #fcfcfc;
    }

    /* 4. CAPITOLE ȘI PARAGRAFE (SUBDIVIZIUNI) */
    .S_CAP_TTL {
        display: block;
        font-family: 'Roboto', sans-serif;
        text-align: center;
        font-weight: 700;
        font-size: 1.1rem;
        color: #7f8c8d;
        margin-top: 50px;
        margin-bottom: 5px;
        text-transform: uppercase;
        letter-spacing: 2px;
    }

    .S_CAP_DEN {
        display: block;
        font-family: 'Roboto', sans-serif;
        text-align: center;
        font-weight: 700;
        font-size: 1.5rem;
        color: #2c3e50;
        margin-bottom: 35px;
        line-height: 1.2;
        padding-bottom: 15px;
        position: relative;
    }

    .S_CAP_DEN::after {
        content: "";
        position: absolute;
        bottom: 0;
        left: 50%;
        transform: translateX(-50%);
        width: 60px;
        height: 3px;
        background-color: #2980b9;
        border-radius: 2px;
    }

    :not(td):not(th) > .S_PAR, .A_PAR
    div:not(table) .S_PAR, .A_PAR {
        display: block !important;
        font-family: 'Roboto', sans-serif;
        font-weight: 700;
        font-size: 1.1rem;
        color: #324151;
        margin: 25px 0 15px 0;
        font-style: italic;
    }

    td .S_PAR, th .S_PAR, td .A_PAR, th .A_PAR {
        display: inline !important;
        font-family: inherit;
        font-weight: 700;
        font-size: inherit;
        margin: 0;
        color: #2c3e50;
        font-style: italic;
    }

    /* 5. ARTICOLE ȘI CONȚINUT */
    .S_ART_TTL, .S_SEC_TTL, .S_ANX_TTL {
        display: inline-block;
        font-family: 'Roboto', sans-serif;
        font-weight: 700;
        color: #2980b9;
        margin-top: 30px;
        font-size: 1.1rem;
    }

    .S_ART_DEN, .S_SEC_DEN, .S_ANX_DEN {
        display: inline-block;
        font-family: 'Merriweather', serif;
        font-weight: 700;
        font-style: italic;
        color: #1a1a1a;
        margin-left: 5px;
        font-size: 1.1rem;
    }

    .S_ART, .S_SEC {
        font-weight: 700;
        margin-top: 25px;
        margin-bottom: 15px;
        color: #000;
        display: block;
        border-bottom: 1px dotted #ccc;
    }

    .S_ALIN {
        display: block;
        margin-bottom: 12px;
        text-align: justify;
        padding-left: 20px;
    }

    .S_LIT, .S_PCT {
        display: block;
        margin-bottom: 8px;
        text-align: justify;
        padding-left: 45px;
    }

    /* 6. NOTE ȘI ANTET */
    .S_HDR {
        font-family: 'Roboto', sans-serif;
        text-align: center;
        font-size: 0.85rem;
        color: #666;
        border-bottom: 1px solid #eee;
        padding-bottom: 20px;
        margin-bottom: 40px;
        text-transform: uppercase;
    }

    .S_NTA_TTL {
        display: block;
        font-family: 'Roboto', sans-serif;
        font-weight: 700;
        font-size: 0.9rem;
        color: #e67e22;
        margin-top: 20px;
        text-transform: uppercase;
    }

    .S_NTA {
        display: block;
        font-family: 'Merriweather', serif;
        font-size: 0.95rem;
        line-height: 1.6;
        color: #5d6d7e;
        background-color: #fffaf0;
        padding: 15px 20px;
        border-left: 3px solid #f39c12;
        margin-bottom: 20px;
        font-style: italic;
    }

    /* 10. GRUPE ȘI PORȚIUNI (S_POR) */
    .S_POR {
        display: block;
        margin: 30px 0;
        padding: 20px;
        border-radius: 4px;
    }

    .S_POR_DEN {
        display: block;
        font-family: 'Roboto', sans-serif;
        font-weight: 700;
        font-size: 1.1rem;
        margin-bottom: 15px;
        text-transform: uppercase;
        line-height: 1.4;
    }

    .S_POR_BDY .S_PAR {
        display: block !important;
        font-family: 'Merriweather', serif !important;
        font-weight: 400 !important;
        font-size: 10pt !important;
        margin: 5px 0 5px 20px !important;
        font-style: normal !important;
        text-align: justify;
    }

    .S_POR_BDY .S_PAR:first-child {
        margin-top: 10px !important;
        font-weight: 700 !important;
    }

    .S_SMN {
        display: block;
        margin-top: 50px;
        margin-bottom: 50px;
        text-align: right;
        padding-right: 50px;
    }

    .S_SMN_PAR {
        display: block;
        font-family: 'Roboto', sans-serif;
        line-height: 1.4;
        text-transform: uppercase;
    }

    .S_SMN_PAR:nth-child(odd) {
        font-weight: 700;
        font-size: 0.9rem;
        color: #7f8c8d;
        margin-top: 20px;
    }

    .S_SMN_PAR:nth-child(even) {
        font-weight: 700;
        font-size: 1.1rem;
        color: #2c3e50;
        margin-bottom: 5px;
        letter-spacing: 1px;
    }

    /* 11. HEADER TIP COPERTĂ DE CARTE (S_BOOK) */
    .S_BOOK_COVER {
        text-align: center;
        padding: 60px 40px;
        margin-bottom: 80px;
        background-color: #ffffff;
        border: 1px solid #eef0f2;
        border-top: 8px solid #2c3e50;
        box-shadow: 0 10px 30px rgba(0,0,0,0.05);
        border-radius: 0 0 4px 4px;
        display: flex;
        flex-direction: column;
        align-items: center;
    }

    .S_BOOK_TITLE {
        font-family: 'Roboto', sans-serif;
        font-weight: 700;
        font-size: 2.5rem;
        color: #2c3e50;
        line-height: 1.2;
        margin: 0 auto 20px auto;
        max-width: 800px;
        text-transform: uppercase;
        letter-spacing: -1px;
    }

    .S_BOOK_DIVIDER {
        width: 80px;
        height: 3px;
        background-color: #e67e22;
        border: none;
        margin: 30px auto;
    }

    .S_BOOK_DESC {
        font-family: 'Merriweather', serif;
        font-size: 1.2rem;
        font-style: italic;
        color: #5d6d7e;
        max-width: 700px;
        margin: 0 auto 30px auto;
        line-height: 1.6;
    }

    .S_BOOK_LINK {
        font-family: 'Roboto', sans-serif;
        font-weight: 700;
        font-size: 0.9rem;
        color: #3498db;
        text-decoration: none;
        text-transform: uppercase;
        letter-spacing: 1px;
        padding: 10px 20px;
        border: 1px solid #3498db;
        border-radius: 50px;
        transition: all 0.3s ease;
        display: inline-block;
        white-space: nowrap;
        max-width: 100%;
        overflow: hidden;
        text-overflow: ellipsis;
        box-sizing: border-box;
    }

    .S_BOOK_LINK:hover {
        background-color: #3498db;
        color: #ffffff;
        box-shadow: 0 4px 12px rgba(52, 152, 219, 0.3);
    }

    /* 9. CUPRINS INTERACTIV (TOC) */
    
    .toc-container {
        background-color: #fafafa;
        border: 1px solid #e0e0e0;
        border-top: 4px solid #2980b9;
        border-radius: 4px;
        padding: 40px;
        margin-bottom: 60px;
        font-family: 'Roboto', sans-serif;
        box-shadow: 0 4px 15px rgba(0,0,0,0.03);
    }

    .toc-title {
        color: #2c3e50;
        font-size: 1.6rem;
        font-weight: 700;
        text-transform: uppercase;
        border-bottom: 2px solid #dcdde1;
        padding-bottom: 15px;
        margin-top: 0;
        margin-bottom: 25px;
        text-align: center;
        letter-spacing: 3px;
    }

    .toc-container ul {
        list-style-type: none;
        padding-left: 0;
        margin: 0;
    }

    .toc-container ul ul {
        padding-left: 25px;
        border-left: 1px dashed #bdc3c7;
        margin-left: 15px;
        margin-top: 5px;
    }

    .toc-container li {
        margin-bottom: 10px;
        line-height: 1.5;
        position: relative;
    }

    .toc-container a {
        text-decoration: none;
        color: #2980b9;
        transition: color 0.2s ease, margin-left 0.2s ease;
        display: inline-block;
    }

    .toc-container a:hover {
        color: #1a5276;
        text-decoration: underline;
    }

    .toc-level-1 {
        margin-top: 25px !important;
    }
    .toc-level-1 > a {
        font-weight: 700;
        color: #2c3e50;
        font-size: 1.15rem;
        text-transform: uppercase;
    }

    .toc-level-2 > a {
        font-weight: 700;
        color: #34495e;
        font-size: 1.05rem;
    }

    .toc-level-3 > a {
        font-weight: 400;
        color: #5d6d7e;
        font-size: 1rem;
    }

    .toc-level-4 > a {
        font-family: 'Merriweather', serif;
        font-style: italic;
        color: #7f8c8d;
        font-size: 0.95rem;
    }

    /* 7. TABELE */
    table {
        display: block;
        width: 100% !important;
        overflow-x: auto;
        border-collapse: collapse;
        margin: 30px 0;
        font-family: 'Roboto', sans-serif;
        font-size: 9pt;
    }

    th, td { border: 1px solid #e0e0e0; padding: 10px; min-width: 80px; }
    th { background-color: #f8f9fa; color: #333; }

    /* =========================================
       8. RESPONSIVE — TABLETĂ (max 850px)
       ========================================= */
    @media (max-width: 850px) {
        body {
            width: 100%;
            padding: 30px 24px;
            font-size: 10pt;
            box-shadow: none;
            border-radius: 0;
        }

        .S_TTL_TTL {
            font-size: 1rem;
            letter-spacing: 2px;
            margin-top: 40px;
        }

        .S_TTL_DEN {
            font-size: 1.4rem;
            margin-bottom: 30px;
            padding: 14px 0;
        }

        .S_CAP_TTL {
            font-size: 0.95rem;
            margin-top: 35px;
            letter-spacing: 1px;
        }

        .S_CAP_DEN {
            font-size: 1.25rem;
            margin-bottom: 25px;
        }

        .S_ART_TTL, .S_SEC_TTL, .S_ANX_TTL,
        .S_ART_DEN, .S_SEC_DEN, .S_ANX_DEN {
            font-size: 1rem;
        }

        :not(td):not(th) > .S_PAR, .A_PAR,
        div:not(table) .S_PAR {
            font-size: 1rem;
            margin: 18px 0 10px 0;
        }

        .S_ALIN {
            padding-left: 12px;
        }

        .S_LIT, .S_PCT {
            padding-left: 24px;
        }

        .toc-container {
            padding: 20px 16px;
            margin-bottom: 35px;
        }

        .toc-title {
            font-size: 1.2rem;
            letter-spacing: 1px;
        }

        .toc-level-1 > a { font-size: 1rem; }
        .toc-level-2 > a { font-size: 0.95rem; }
        .toc-level-3 > a { font-size: 0.9rem; }
        .toc-level-4 > a { font-size: 0.88rem; }

        .toc-container ul ul {
            padding-left: 14px;
            margin-left: 8px;
        }

        .S_BOOK_COVER {
            padding: 30px 20px;
            margin-bottom: 40px;
            align-items: center;
        }

        .S_BOOK_TITLE {
            font-size: 1.6rem;
            letter-spacing: 0;
        }

        .S_BOOK_DESC {
            font-size: 1rem;
        }

        .S_SMN {
            padding-right: 0;
            text-align: left;
            margin-top: 30px;
            margin-bottom: 30px;
        }

        .S_POR {
            padding: 14px;
            margin: 20px 0;
        }

        th, td {
            padding: 7px 8px;
        }

        #scrollTopBtn {
            bottom: 16px;
            right: 16px;
            width: 42px;
            height: 42px;
            padding: 10px;
            font-size: 16px;
        }

        #search-open-btn {
            bottom: 68px;
            right: 16px;
            padding: 11px;
        }
    }

    /* =========================================
       RESPONSIVE — MOBIL MIC (max 480px)
       ========================================= */
    @media (max-width: 480px) {
        body {
            padding: 20px 16px;
            font-size: 9.5pt;
            line-height: 1.65;
        }

        .S_TTL_TTL {
            font-size: 0.85rem;
            letter-spacing: 1px;
            margin-top: 28px;
        }

        .S_TTL_DEN {
            font-size: 1.15rem;
            margin-bottom: 22px;
            padding: 10px 0;
        }

        .S_CAP_TTL {
            font-size: 0.82rem;
            margin-top: 26px;
        }

        .S_CAP_DEN {
            font-size: 1.05rem;
            margin-bottom: 18px;
        }

        .S_ART_TTL, .S_SEC_TTL, .S_ANX_TTL,
        .S_ART_DEN, .S_SEC_DEN, .S_ANX_DEN {
            font-size: 0.9rem;
        }

        .S_ALIN {
            padding-left: 8px;
        }

        .S_LIT, .S_PCT {
            padding-left: 18px;
        }

        .toc-container {
            padding: 16px 12px;
            margin-bottom: 26px;
        }

        .toc-title {
            font-size: 1rem;
        }

        .toc-level-1 > a { font-size: 0.9rem; }
        .toc-level-2 > a { font-size: 0.88rem; }
        .toc-level-3 > a { font-size: 0.85rem; }
        .toc-level-4 > a { font-size: 0.82rem; }

        .toc-container ul ul {
            padding-left: 10px;
            margin-left: 6px;
        }

        .S_BOOK_COVER {
            padding: 20px 14px;
            margin-bottom: 28px;
            align-items: center;
        }

        .S_BOOK_TITLE {
            font-size: 1.3rem;
        }

        .S_BOOK_DESC {
            font-size: 0.9rem;
        }

        .S_NTA {
            padding: 10px 14px;
            font-size: 0.88rem;
        }

        .S_POR {
            padding: 10px;
        }

        table {
            font-size: 8pt;
            margin: 16px 0;
        }

        th, td {
            padding: 5px 6px;
            min-width: 60px;
        }

    }

    /* Progressive rendering for long legislative documents */
    .S_ART,
    .S_SEC,
    .S_CAP,
    div[class*="S_"] {  
        content-visibility: auto;
        contain-intrinsic-size: auto 300px;
    }

    /* Optional: make it even better for very tall sections */
    .S_ART:nth-child(n + 5) {   
        contain-intrinsic-size: auto 800px;  
    }

    /* PRINT */
    @media print {
        body { background: white; padding: 0; font-size: 10pt; }
        .container, body > div { box-shadow: none; width: 100%; padding: 0; }
        .S_TTL_DEN, .S_CAP_DEN { page-break-after: avoid; }
        .S_BOOK_COVER { box-shadow: none; border: 1px solid #ccc; margin-bottom: 40px; padding: 30px; }
        .S_BOOK_LINK { display: none; }
    }
"""

SCROLL_BTN_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="size-6">
  <path stroke-linecap="round" stroke-linejoin="round" d="M4.5 10.5 12 3m0 0 7.5 7.5M12 3v18" />
</svg>
"""


@dataclass
class LegislatieJustCleaner:
    """Step 2: normalize raw HTML and build a clean, semantic HTML document."""

    surse: list[Sursa] = field(default_factory=lambda: list(surse_legislatie_just))
    data_dir: str | Path | None = None
    raw_dir: str | Path | None = None
    clean_dir: str | Path | None = None
    limit: int | None = None
    force: bool = False
    use_ocr: bool = True

    def __post_init__(self):
        root = resolve_data_dir(self.data_dir)
        self.raw_dir = (
            Path(self.raw_dir).expanduser().resolve()
            if self.raw_dir
            else step_dir(root, "raw")
        )
        self.clean_dir = (
            Path(self.clean_dir).expanduser().resolve()
            if self.clean_dir
            else step_dir(root, "clean")
        )
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.clean_dir.mkdir(parents=True, exist_ok=True)

        # 1. Define the centralized cache directory
        self.ocr_cache_dir = Path(tempfile.gettempdir()) / "ljust_docling_ocr_cache"

        # 2. If it exists, wipe it to ensure a clean start
        if self.ocr_cache_dir.exists():
            shutil.rmtree(self.ocr_cache_dir)

        # 3. Create the fresh directory
        self.ocr_cache_dir.mkdir(parents=True, exist_ok=True)

    def inject_head(self, soup: BeautifulSoup):
        # 1. Strip existing head tags
        for element in soup.head.find_all(
            ["style", "link", "meta", "title", "html", "body"]
        ):
            element.decompose()

        # 2. Viewport meta
        new_meta_vp = soup.new_tag(
            "meta", content="width=device-width, initial-scale=1"
        )
        new_meta_vp.attrs["name"] = "viewport"
        soup.head.append(new_meta_vp)

        # 3. External links + script srcs
        fragment = BeautifulSoup(HEAD_LINKS, "html.parser")
        for tag in list(fragment.children):
            soup.head.append(tag)

        # 5. Scroll-to-top JS — same treatment
        scroll_script = soup.new_tag("script")
        scroll_script.append(NavigableString(SCROLL_JS))
        soup.head.append(scroll_script)

        # 6. CSS — NavigableString keeps ::before, content:"", etc. intact
        style_tag = soup.new_tag("style")
        style_tag.append(NavigableString(STYLES_CSS))
        soup.head.append(style_tag)

        # 7. Scroll-to-top button in body
        scroll_btn = soup.new_tag(
            "button",
            attrs={"onclick": "topFunction()", "id": "scrollTopBtn", "title": "Sus"},
        )
        svg_fragment = BeautifulSoup(SCROLL_BTN_SVG, "html.parser").svg
        scroll_btn.append(svg_fragment)
        soup.body.append(scroll_btn)

        return soup

    def remove_inline_text_blue_style(self, soup: BeautifulSoup):
        for tag in soup.find_all(attrs={"style": True}):
            s = tag["style"].lower()
            if "color:blue" in s or "color: blue" in s:
                del tag["style"]
        return soup

    def remove_empty_spans(self, soup: BeautifulSoup):
        for span in soup.find_all("span"):
            if not span.get_text(strip=True):
                span.decompose()
        return soup

    def remove_paranthesis_paragraphs(self, soup: BeautifulSoup):
        for span in soup.find_all("span", class_="S_PAR"):
            text = span.get_text().strip()
            if text.startswith("(") and text.endswith(")"):
                span.decompose()
        return soup

    def remove_ellipsis_paragraphs(self, soup: BeautifulSoup):
        # remove spans that contain ONLY &nbsp;...&nbsp;
        for span in soup.find_all("span"):
            # get text, replace non-breaking spaces with normal spaces, then strip
            text = span.get_text().replace("\xa0", " ").strip()
            if text == "...":
                span.decompose()

        # remove: <span style="display: none"> ... </span>
        for span in soup.find_all("span", style=lambda s: s and "display: none" in s):
            span.decompose()

        return soup

    def remove_abrogat_blocks(self, soup: BeautifulSoup):
        # remove all lines not valid anymore
        for clsname in ["S_PAR", "S_ALN", "S_PCT", "S_LIT", "S_ART", "S_CAP", "S_SEC"]:
            for bdy_span in soup.find_all("span", class_=f"{clsname}_BDY"):
                # Get all direct text children, excluding nested spans
                direct_texts = []
                for child in bdy_span.children:
                    if isinstance(child, str):
                        text = child.strip().replace("\xa0", " ")
                        if text:
                            direct_texts.append(text)
                    elif child.name == "span" and child.get("class"):
                        # Check if it's a S_PAR with parenthetical content
                        if "S_PAR" in child.get("class", []):
                            child_text = child.get_text().strip()
                            if not (
                                child_text.startswith("(") and child_text.endswith(")")
                            ):
                                direct_texts.append(child_text.replace("\xa0", " "))

                # Check if the first meaningful text starts with "Abrogat" or "Abrogată"
                if direct_texts and (
                    direct_texts[0].startswith("Abrogat")
                    or direct_texts[0].startswith("Abrogată")
                ):
                    parent_span = bdy_span.find_parent("span", class_=clsname)
                    if parent_span:
                        parent_span.decompose()
                        continue

        # After removing abrogated content, clean up empty parent structures
        # This second pass ensures that if all children of a section/chapter were removed, the parent is also removed
        for clsname in ["S_SEC", "S_CAP"]:
            for elem in soup.find_all("span", class_=clsname):
                # Check if this section/chapter has any meaningful content left
                body = elem.find("span", class_=f"{clsname}_BDY")
                if body:
                    # Get all child structural elements
                    has_content = False
                    for child_class in ["S_ART", "S_PAR", "S_ALN", "S_PCT", "S_LIT"]:
                        if body.find("span", class_=child_class):
                            has_content = True
                            break

                    # If no structural children remain, check for direct text
                    if not has_content:
                        text = body.get_text(strip=True).replace("\xa0", " ")
                        # If empty or only contains "Abrogat"/"Abrogată"
                        if not text or text.startswith("Abrogat"):
                            elem.decompose()

        return soup

    def remove_unused_attrs(self, soup: BeautifulSoup):
        bad_attributes = [
            "id",
            "onmousedown",
            "onmouseout",
            "onmouseover",
            "style",
            "cellpadding",
            "cellspacing",
            "valign",
            "width",
            "height",
            "name",
            "rowspan",
            "colspan",
            "align",
        ]
        for tag in soup.find_all(True):
            for attr in bad_attributes:
                tag.attrs.pop(attr, None)

        return soup

    def remove_br_tags(self, soup: BeautifulSoup):
        for br in soup.find_all("br"):
            br.decompose()
        return soup

    def remove_abrogated_annexes(self, soup: BeautifulSoup):
        for anx in soup.find_all("span", class_="S_ANX"):
            clean_text = anx.get_text(strip=True).lower()
            if "abrogata" in clean_text:
                anx.decompose()
        return soup

    def generate_table_of_contents(self, soup: BeautifulSoup):

        # 1. Creăm containerul principal aplicând noua clasă CSS
        toc_container = soup.new_tag(
            "div",
            attrs={"class": "toc-container", "id": "cuprins-interactiv"},
        )

        # Aplicăm clasa pentru titlu
        toc_title = soup.new_tag("h2", attrs={"class": "toc-title"})
        toc_title.string = "Cuprins"
        toc_container.append(toc_title)

        toc_root_ul = soup.new_tag("ul")
        toc_container.append(toc_root_ul)

        ierarhie_ul = {1: toc_root_ul, 2: None, 3: None, 4: None}

        def get_nivel_ierarhic(prefix):
            if prefix in ["S_TTL", "S_ANX"]:
                return 1
            if prefix == "S_CAP":
                return 2
            if prefix == "S_SEC":
                return 3
            if prefix == "S_ART":
                return 4
            return 1

        blocuri = soup.find_all(class_=["S_TTL", "S_CAP", "S_SEC", "S_ART", "S_ANX"])

        for index, bloc in enumerate(blocuri):
            clase = bloc.get("class", [])
            prefix = next(
                (
                    c
                    for c in clase
                    if c in ["S_TTL", "S_CAP", "S_SEC", "S_ART", "S_ANX"]
                ),
                None,
            )

            if not prefix:
                continue
            nivel = get_nivel_ierarhic(prefix)

            ttl_elem = bloc.find(class_=f"{prefix}_TTL")
            ttl_text = ttl_elem.get_text(strip=True) if ttl_elem else ""

            id_ancora = f"ancora_{prefix.lower()}_{index}"
            if ttl_elem:
                ttl_elem["id"] = id_ancora
            else:
                bloc["id"] = id_ancora

            den_text = ""
            if prefix == "S_ART":
                par_elem = bloc.find(class_="S_PAR")
                if par_elem:
                    den_text = par_elem.get_text(strip=True)
                    if len(den_text) > 80:
                        den_text = den_text[:77] + "..."
            else:
                den_elem = bloc.find(class_=f"{prefix}_DEN")
                if den_elem:
                    den_text = den_elem.get_text(strip=True)

            link_text = ttl_text
            if den_text:
                link_text += f" - {den_text}" if link_text else den_text

            if not link_text:
                continue

            # Aici aplicăm clasa CSS dinamică în funcție de nivelul ierarhic
            li = soup.new_tag("li", attrs={"class": f"toc-level-{nivel}"})

            a = soup.new_tag("a", href=f"#{id_ancora}")
            a.string = link_text
            li.append(a)

            target_level = nivel
            while target_level >= 1:
                if ierarhie_ul[target_level] is not None:
                    target_ul = ierarhie_ul[target_level]
                    break
                target_level -= 1

            target_ul.append(li)

            if nivel < 4:
                # Eliminăm stilurile inline, CSS-ul creat mai sus se va ocupa de indentări și linii punctate
                nou_ul = soup.new_tag("ul")
                li.append(nou_ul)
                ierarhie_ul[nivel + 1] = nou_ul
                for i in range(nivel + 2, 5):
                    ierarhie_ul[i] = None

        soup.body.insert(0, toc_container)

        return soup

    def extract_file_metadata(self, file_path: Path):
        title, description, link, link_name = None, None, None, None
        meta = extract_file_meta(file_path.name)

        if not meta:
            return None, None, None, None

        number = meta["numar_link"]
        slug = meta["slug"]
        date_iso = meta["data_consolidarii"]
        sursa = None
        for s in self.surse:
            if s.slug == slug:
                sursa = Sursa(
                    nume=s.nume, descriere=s.descriere, numar_link=int(number), slug=s.slug
                )
                break

        if sursa is None:
            return None, None, None, None

        title, description, link, link_name = (
            sursa.nume,
            sursa.descriere,
            sursa.link,
            f"Sursa: {sursa.slug}-{date_iso}",
        )

        return title, description, link, link_name

    def insert_source_info(self, soup: BeautifulSoup, html_path: Path):

        title, description, link, link_name = self.extract_file_metadata(html_path)

        if not all([title, description, link, link_name]):
            return soup

        # Creăm containerul principal
        header = soup.new_tag("header", attrs={"class": "S_BOOK_COVER"})

        # Titlul (Titlu mare de carte)
        h1 = soup.new_tag("h1", attrs={"class": "S_BOOK_TITLE"})
        h1.string = title
        header.append(h1)

        # Separator vizual (Linia decorativă)
        line = soup.new_tag("hr", attrs={"class": "S_BOOK_DIVIDER"})
        header.append(line)

        # Descrierea
        p = soup.new_tag("p", attrs={"class": "S_BOOK_DESC"})
        p.string = description
        header.append(p)

        # Link-ul către sursă
        a = soup.new_tag(
            "a", href=link, attrs={"class": "S_BOOK_LINK", "target": "_blank"}
        )

        svg_link_code = """
        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="size-6">
            <path stroke-linecap="round" stroke-linejoin="round" d="M13.19 8.688a4.5 4.5 0 0 1 1.242 7.244l-4.5 4.5a4.5 4.5 0 0 1-6.364-6.364l1.757-1.757m13.35-.622 1.757-1.757a4.5 4.5 0 0 0-6.364-6.364l-4.5 4.5a4.5 4.5 0 0 0 1.242 7.244" />
        </svg>
        """
        svg_tag = BeautifulSoup(svg_link_code, "html.parser").svg
        a.append(svg_tag)
        a.append(f" {link_name}")
        header.append(a)

        soup.body.insert(0, header)

        return soup

    def add_ids_and_resolve_links(self, soup: BeautifulSoup) -> BeautifulSoup:
        """
        Assigns IDs and converts S_LGI references into clickable links.
        Handles combined references (art/pct/aln/lit), enumerators (si/sau/,),
        bare article numbers (e.g., "291^3"), and ^ annotations.
        """
        # 1. ASSIGN IDs
        for cls in ("S_SEC", "S_ART", "S_ALN", "S_LIT", "S_PCT"):
            for idx, el in enumerate(soup.find_all("span", class_=cls), start=1):
                if not el.get("id"):
                    el["id"] = f"{cls.lower()}_{idx}"

        # 2. BUILD REFERENCE INDEX
        art_idx: dict[int, str] = {}
        aln_idx: dict[tuple[int, str], str] = {}
        pct_idx: dict[tuple[int, str], str] = {}
        lit_idx: dict[tuple[int, str, str, str], str] = {}

        for art in soup.find_all("span", class_="S_ART"):
            ttl = art.find("span", class_="S_ART_TTL")
            m = re.search(r"\d+", ttl.get_text()) if ttl else None
            art_num = int(m.group()) if m else None
            if art_num is None:
                continue
            art_idx[art_num] = art["id"]

            # Index S_ALN & nested S_LIT
            for aln in art.find_all("span", class_="S_ALN"):
                ttl = aln.find("span", class_="S_ALN_TTL")
                m = re.search(r"(\d+(?:\^[a-z0-9]+)?)", ttl.get_text()) if ttl else None
                aln_key = m.group(1) if m else None
                if aln_key:
                    aln_idx[(art_num, aln_key)] = aln["id"]
                    for lit in aln.find_all("span", class_="S_LIT"):
                        lit_ttl = lit.find("span", class_="S_LIT_TTL")
                        if lit_ttl:
                            lit_char = re.sub(
                                r"[^a-z0-9^]", "", lit_ttl.get_text()
                            ).lower()
                            if lit_char:
                                lit_idx[(art_num, "aln", aln_key, lit_char)] = lit["id"]

            # Index S_PCT & nested S_LIT
            for pct in art.find_all("span", class_="S_PCT"):
                ttl = pct.find("span", class_="S_PCT_TTL")
                m = re.search(r"(\d+(?:\^[a-z0-9]+)?)", ttl.get_text()) if ttl else None
                pct_key = m.group(1) if m else None
                if pct_key:
                    pct_idx[(art_num, pct_key)] = pct["id"]
                    for lit in pct.find_all("span", class_="S_LIT"):
                        lit_ttl = lit.find("span", class_="S_LIT_TTL")
                        if lit_ttl:
                            lit_char = re.sub(
                                r"[^a-z0-9^]", "", lit_ttl.get_text()
                            ).lower()
                            if lit_char:
                                lit_idx[(art_num, "pct", pct_key, lit_char)] = lit["id"]

        # 3. PROCESS S_LGI ELEMENTS (unchanged call)
        for lgi in soup.find_all("span", class_="S_LGI"):
            raw_text = lgi.get_text()
            if not raw_text.strip():
                continue

            parent_art = lgi.find_parent("span", class_="S_ART")
            ctx_art = None
            if parent_art:
                ttl = parent_art.find("span", class_="S_ART_TTL")
                m = re.search(r"\d+", ttl.get_text()) if ttl else None
                if m:
                    ctx_art = int(m.group())

            new_content = self._resolve_and_link_ref(
                raw_text, ctx_art, art_idx, aln_idx, lit_idx, pct_idx, soup
            )
            lgi.clear()
            lgi.extend(new_content)

        return soup

    def _resolve_and_link_ref(
        self,
        ref_text: str,
        ctx_art: int | None,
        art_idx,
        aln_idx,
        lit_idx,
        pct_idx,
        soup: BeautifulSoup,
    ) -> list[Tag | NavigableString]:
        """
        Improved parser: now correctly handles compound references inside a single part
        (e.g. "alin. (2) lit. b)", "pct. 3 lit. a)", "art. 290 alin. (1) lit. c)").
        Each sub-reference becomes its own clickable <a> tag.
        """
        norm = re.sub(r"\s+", " ", ref_text.strip())
        # Split only on top-level separators, keep them for output
        parts = re.split(r"(?i)\s*(,|\bsi\b|\bsau\b)\s*", norm)

        result: list[Tag | NavigableString] = []

        # Current context (persists across parts and inside compound phrases)
        cur_art = ctx_art
        cur_aln: str | None = None
        cur_pct: str | None = None
        cur_parent_type: str | None = None

        # ------------------------------------------------------------------
        # Patterns (left-to-right matching). Order in list doesn't matter —
        # we always take the leftmost match.
        # ------------------------------------------------------------------
        art_re = re.compile(r"(?i)art(?:icolul)?\.?\s*(\d+)")
        aln_re = re.compile(
            r"(?i)alin(?:eatul)?\.?\s*\(?\s*(\d+(?:\^[a-z0-9]+)?)\s*\)?"
        )
        pct_re = re.compile(r"(?i)pct\.?\s*(\d+(?:\^[a-z0-9]+)?)")
        lit_re = re.compile(r"(?i)(?:lit(?:era)?\.?\s*)\(?([a-z](?:\^[a-z0-9]+)?)\)?")
        bare_lit_re = re.compile(r"(?i)\b\(?\s*([a-z](?:\^[a-z0-9]+)?)\s*\)?")
        bare_re = re.compile(r"(?i)\b\(?\s*(\d+(?:\^[a-z0-9]+)?)\s*\)?")

        patterns = [
            (art_re, "art", lambda m: ("art", int(m.group(1)))),
            (aln_re, "aln", lambda m: ("aln", m.group(1))),
            (pct_re, "pct", lambda m: ("pct", m.group(1))),
            (lit_re, "lit", lambda m: ("lit", m.group(1).lower())),
            (bare_lit_re, "lit", lambda m: ("lit", m.group(1).lower())),
            (bare_re, "bare", lambda m: ("bare", m.group(1))),
        ]

        for i, part in enumerate(parts):
            if i % 2 == 1:  # separator
                result.append(NavigableString(f" {part} "))
                continue

            part = part.strip()
            if not part:
                continue

            remaining = part
            while remaining:
                matches = []
                for regex, ref_type, extractor in patterns:
                    m = regex.search(remaining)
                    if m:
                        matches.append((m.start(), m.end(), ref_type, extractor(m)))

                if not matches:
                    # leftover text (should be rare)
                    result.append(NavigableString(remaining))
                    break

                # Take leftmost match
                matches.sort(key=lambda x: x[0])
                start, end, ref_type, (typ, val) = matches[0]

                # Text before this sub-reference (usually whitespace)
                if start > 0:
                    result.append(NavigableString(remaining[:start]))

                matched_text = remaining[start:end]

                # === Resolve & update context ===
                target_id = None

                if typ == "art":
                    cur_art = val
                    cur_parent_type = "art"
                    target_id = art_idx.get(cur_art)

                elif typ == "pct":
                    cur_pct = val
                    cur_parent_type = "pct"
                    target_id = pct_idx.get((cur_art, val))
                    if not target_id:
                        base = (
                            re.match(r"(\d+)", val).group(1)
                            if re.match(r"(\d+)", val)
                            else val
                        )
                        target_id = pct_idx.get((cur_art, base))

                elif typ == "aln":
                    cur_aln = val
                    cur_parent_type = "aln"
                    target_id = aln_idx.get((cur_art, val))
                    if not target_id:
                        base = (
                            re.match(r"(\d+)", val).group(1)
                            if re.match(r"(\d+)", val)
                            else val
                        )
                        target_id = aln_idx.get((cur_art, base))

                elif typ == "lit":
                    lit_val = val
                    parent_type = cur_parent_type or "aln"
                    parent_num = cur_aln if parent_type == "aln" else cur_pct
                    if parent_num:
                        target_id = lit_idx.get(
                            (cur_art, parent_type, parent_num, lit_val)
                        )
                        if not target_id:
                            base_m = re.match(r"[a-z]", lit_val)
                            if base_m:
                                target_id = lit_idx.get(
                                    (cur_art, parent_type, parent_num, base_m.group(0))
                                )

                elif typ == "bare":
                    # Same heuristic logic you already had
                    is_paren = matched_text.strip().startswith(
                        "("
                    ) or matched_text.strip().endswith(")")
                    if cur_parent_type == "pct":
                        target_id = pct_idx.get((cur_art, val))
                        if not target_id:
                            base = (
                                re.match(r"(\d+)", val).group(1)
                                if re.match(r"(\d+)", val)
                                else val
                            )
                            target_id = pct_idx.get((cur_art, base))
                        cur_pct = val
                    elif cur_parent_type == "aln":
                        target_id = aln_idx.get((cur_art, val))
                        if not target_id:
                            base = (
                                re.match(r"(\d+)", val).group(1)
                                if re.match(r"(\d+)", val)
                                else val
                            )
                            target_id = aln_idx.get((cur_art, base))
                        cur_aln = val
                    elif cur_parent_type == "art":
                        base_val = (
                            int(re.match(r"(\d+)", val).group(1))
                            if re.match(r"\d+", val)
                            else None
                        )
                        if base_val is not None:
                            target_id = art_idx.get(base_val)
                            if target_id:
                                cur_art = base_val
                    elif is_paren:
                        target_id = aln_idx.get((cur_art, val))
                        if not target_id:
                            base = (
                                re.match(r"(\d+)", val).group(1)
                                if re.match(r"(\d+)", val)
                                else val
                            )
                            target_id = aln_idx.get((cur_art, base))
                        if target_id:
                            cur_aln = val
                            cur_parent_type = "aln"
                    else:
                        # default to article, fallback to aln
                        base_val = (
                            int(re.match(r"(\d+)", val).group(1))
                            if re.match(r"\d+", val)
                            else None
                        )
                        if base_val is not None:
                            target_id = art_idx.get(base_val)
                            if target_id:
                                cur_art = base_val
                            else:
                                target_id = aln_idx.get((cur_art, val))
                                if not target_id:
                                    base = (
                                        re.match(r"(\d+)", val).group(1)
                                        if re.match(r"\d+", val)
                                        else val
                                    )
                                    target_id = aln_idx.get((cur_art, base))
                                if target_id:
                                    cur_aln = val
                                    cur_parent_type = "aln"

                # === Create link or plain text ===
                if target_id:
                    a_tag = soup.new_tag(
                        "a", attrs={"href": f"#{target_id}", "class": "S_LGI"}
                    )
                    a_tag.string = matched_text
                    result.append(a_tag)
                else:
                    result.append(NavigableString(matched_text))

                remaining = remaining[end:]

        return result

    def replace_base64_img_with_ocr_result_img(self, soup: BeautifulSoup):
        images = soup.find_all("img")

        for img in images:
            src = img.get("src")
            if not src:
                continue

            # Determine if the image source is a base64 data URI
            is_base64 = src.startswith("data:image/") and "base64," in src
            if not is_base64:
                continue

            log.debug("Parsing image...")

            # --- CACHING LOGIC ---
            # 1. Create a SHA256 hash of the base64 string
            img_hash = hashlib.sha256(src.encode("utf-8")).hexdigest()
            cache_file = self.ocr_cache_dir / f"{img_hash}.md"

            ocr_text = ""

            # 2. Check if we already processed this exact image
            if cache_file.exists():
                log.debug(f"Found cache for image {img_hash[:8]}...")
                ocr_text = cache_file.read_text(encoding="utf-8")
            else:
                log.debug(
                    f"No cache for current file. Running OCR for {img_hash[:8]}..."
                )
                # 3. Extract the OCR text dynamically based on the source type
                ocr_text = extract_table_from_image_base64(src)

                # 4. Save to cache atomically (Thread/Process safe)
                if ocr_text:
                    # Write to a random temporary file first
                    temp_cache = (
                        self.ocr_cache_dir / f"{img_hash}_{uuid.uuid4().hex}.tmp"
                    )
                    temp_cache.write_text(ocr_text, encoding="utf-8")
                    # Rename is an atomic operation on most OSs.
                    # If two processes do this at the exact same time, the file just gets overwritten safely.
                    temp_cache.replace(cache_file)
            # ----------------------

            # 5. Setup the new container
            new_div = soup.new_tag("div", attrs={"class": "ocr-result-container"})

            # 6. Create and append the new <img> tag
            new_img_tag = soup.new_tag("img", src=src)
            new_img_tag["style"] = "max-width: 100%; height: auto;"
            new_div.append(new_img_tag)

            # 7. Create a wrapper for the text
            if ocr_text:
                text_container = soup.new_tag(
                    "div", attrs={"class": "ocr-text-wrapper"}
                )

                # Convert markdown to an HTML string
                html_table_string = markdown.markdown(ocr_text, extensions=["tables"])

                # Parse the HTML string into BeautifulSoup tags
                parsed_table_html = BeautifulSoup(html_table_string, "html.parser")

                # Append the parsed tags, NOT a literal string
                text_container.append(parsed_table_html)
                new_div.append(text_container)

            # 8. Replace the old <img> with our new complex <div>
            img.replace_with(new_div)

            log.debug("Done parsing image!")

        return soup

    def make_semantic_html(self, soup: BeautifulSoup):

        # 1. SMART SMASH (Specifically for S_LGI)
        for lgi in soup.find_all("span", class_="S_LGI"):
            for child in list(lgi.children):
                if isinstance(child, str):
                    # 1. If it's just whitespace/newlines, turn it into a single space
                    if not child.strip():
                        child.replace_with(" ")
                    else:
                        # 2. If it has text, remove the 'letter-stacking' newlines
                        # but preserve a space if it was at the start or end
                        starts_space = child.startswith(("\n", " "))
                        ends_space = child.endswith(("\n", " "))

                        cleaned = child.replace("\n", "").strip()

                        if starts_space:
                            cleaned = " " + cleaned
                        if ends_space:
                            cleaned = cleaned + " "

                        child.replace_with(cleaned)

                elif child.name == "a":
                    # Clean link text but keep it as a distinct block
                    child.string = child.get_text().replace("\n", "").strip()

            # Merge the string nodes so they don't look like separate entities
            lgi.smooth()

            # Final check: Collapse multiple spaces into one inside the tag
            final_text = lgi.get_text()
            if "  " in final_text:
                # This is a bit of a hack to clean up the internal text
                # while keeping the <a> tags intact.
                for nav_str in lgi.find_all(string=True):
                    nav_str.replace_with(nav_str.replace("  ", " "))

        # 1. Map the structural containers to semantic tags
        mapping = {
            "S_TTL": "section",  # Title (Major part of Law)
            "S_CAP": "section",  # Chapter
            "S_SEC": "section",  # Section
            "S_ART": "article",  # Article
            "S_ANX": "section",  # Annex
        }

        for cls, tag in mapping.items():
            for el in soup.find_all("span", class_=cls):
                el.name = tag

        # 2. Convert Header elements (TTL/DEN) to actual Headings
        # Note: We use hierarchy H1 -> H2 -> H3 based on your list
        headers = {
            "S_TTL_TTL": "h1",
            "S_TTL_DEN": "h1",
            "S_CAP_TTL": "h2",
            "S_CAP_DEN": "h2",
            "S_SEC_TTL": "h3",
            "S_SEC_DEN": "h3",
            "S_ART_TTL": "h3",
            "S_ANX_TTL": "h3",
            "S_ANX_DEN": "h3",
        }

        for cls, tag in headers.items():
            for el in soup.find_all("span", class_=cls):
                el.name = tag

        # 3. Handle List Items (LIT, PCT, ALN, LIN)
        # These often act as <li> but are nested in the source
        list_items = ["S_LIT", "S_PCT", "S_ALN", "S_LIN"]
        for cls in list_items:
            for el in soup.find_all("span", class_=cls):
                el.name = "li"

        # 4. Handle Titles inside list items (e.g., (1), a., 1.)
        list_titles = ["S_LIT_TTL", "S_PCT_TTL", "S_ALN_TTL", "S_LIN_TTL"]
        for cls in list_titles:
            for el in soup.find_all("span", class_=cls):
                el.name = "strong"

        # 5. Handle Paragraphs and References
        for el in soup.find_all("span", class_="S_PAR"):
            el.name = "p"

        for el in soup.find_all("span", class_="S_LGI"):
            el.name = "i"

        # 6. UNWRAP "Body" spans and structural noise
        # These just add depth without adding semantic value
        wrappers = [
            "S_TTL_BDY",
            "S_CAP_BDY",
            "S_SEC_BDY",
            "S_ART_BDY",
            "S_ANX_BDY",
            "S_LIT_BDY",
            "S_PCT_BDY",
            "S_ALN_BDY",
            "S_LIN_BDY",
        ]
        for cls in wrappers:
            for el in soup.find_all("span", class_=cls):
                el.unwrap()

        # 7. Post-Processing: Wrap floating <li> into <ul> instead of <ol>
        # We use <ul> to prevent Markdown converters from adding "1. 2. 3."
        for li in soup.find_all("li"):
            # Check if it's already inside a list
            if li.parent.name not in ["ol", "ul"]:
                # Find all consecutive sibling <li> elements to group them in one <ul>
                # (This is a more advanced way to avoid a <ul> for every single <li>)
                siblings = [li]
                for next_node in li.find_next_siblings():
                    if next_node.name == "li":
                        siblings.append(next_node)
                    else:
                        break

                # Create the <ul> wrapper
                new_list = soup.new_tag("ul", style="list-style-type: none;")
                li.wrap(new_list)

                # Move siblings into the same <ul>
                for sibling in siblings[1:]:
                    new_list.append(sibling)

        return soup

    def add_ids_to_remanining_elements(self, soup: BeautifulSoup):
        par_pattern = re.compile(r".*_PAR$")

        counter = 1
        for tag in soup.find_all(class_=par_pattern):
            if not tag.has_attr("id"):
                tag["id"] = f"v-{counter}"
                counter += 1

        return soup

    def cleanify(self) -> dict:
        processed = 0
        skipped = 0
        slugs = {s.slug for s in self.surse}

        for html_path in sorted(self.raw_dir.glob("*.html")):
            if self.limit is not None and processed >= self.limit:
                break

            meta = extract_file_meta(html_path.name)
            if not meta or meta["slug"] not in slugs:
                continue

            clean_path = self.clean_dir / html_path.name
            if not self.force and clean_path.exists():
                log.debug(f"Skip. Avem curatat deja sursa {html_path}")
                skipped += 1
                continue

            log.debug(f"Cleaning up: {html_path}")

            html_text = unidecode(html_path.read_text())
            html_text = re.sub(r"<!---->", "", html_text)

            soup = BeautifulSoup(html_text, "html5lib")

            soup.html["lang"] = "ro"

            soup = self.remove_inline_text_blue_style(soup)
            soup = self.remove_empty_spans(soup)
            soup = self.remove_paranthesis_paragraphs(soup)
            soup = self.remove_ellipsis_paragraphs(soup)
            soup = self.remove_abrogat_blocks(soup)
            soup = self.remove_unused_attrs(soup)
            soup = self.remove_br_tags(soup)
            soup = self.remove_abrogated_annexes(soup)
            soup = self.inject_head(soup)
            if self.use_ocr:
                soup = self.replace_base64_img_with_ocr_result_img(soup)
            soup = self.generate_table_of_contents(soup)
            soup = self.add_ids_and_resolve_links(soup)
            soup = self.insert_source_info(soup, html_path)
            soup = self.make_semantic_html(soup)
            soup = self.add_ids_to_remanining_elements(soup)

            clean_path.write_text(soup.prettify(), encoding="utf-8")
            processed += 1

        return {"cleaned": processed, "skipped": skipped}
