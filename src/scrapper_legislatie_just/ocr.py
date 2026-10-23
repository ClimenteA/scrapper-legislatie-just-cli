"""OCR helpers used by the cleaner for tables embedded as base64 images.

The heavy Docling/EasyOCR imports are intentionally lazy: scraping, cleaning
documents without images should not pay the import cost.
"""

import base64
import tempfile
from pathlib import Path

from .logger import log


def extract_table_from_image_base64(base64_data_uri: str) -> str:
    """Run Docling OCR on a base64 image and return Markdown tables/text."""
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (
            AcceleratorDevice,
            AcceleratorOptions,
            EasyOcrOptions,
            PdfPipelineOptions,
        )
        from docling.document_converter import DocumentConverter, ImageFormatOption

        if "base64," in base64_data_uri:
            header, base64_payload = base64_data_uri.split("base64,", 1)
            ext = "." + header.split("/")[1].split(";")[0]
        else:
            base64_payload = base64_data_uri
            ext = ".jpg"

        image_bytes = base64.b64decode(base64_payload)

        pipeline_options = PdfPipelineOptions()
        pipeline_options.accelerator_options = AcceleratorOptions(
            device=AcceleratorDevice.CPU
        )
        pipeline_options.ocr_options = EasyOcrOptions(lang=["ro", "en"])
        pipeline_options.do_table_structure = True
        pipeline_options.table_structure_options.do_cell_matching = True

        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_path = Path(tmp_dir) / f"ocr_input{ext}"
            temp_path.write_bytes(image_bytes)

            converter = DocumentConverter(
                format_options={
                    InputFormat.IMAGE: ImageFormatOption(
                        pipeline_options=pipeline_options
                    )
                }
            )
            result = converter.convert(temp_path)

            return result.document.export_to_markdown()

    except Exception as ex:
        log.exception(f"Docling extraction failed: {ex}")
        return ""
