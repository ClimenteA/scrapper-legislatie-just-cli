import os
import tomllib
from datetime import date
from pathlib import Path

from pydantic import BaseModel, model_validator
from unidecode import unidecode

SOURCES_TOML_PATH = Path(__file__).with_name("sources.toml")


class Sursa(BaseModel):
    nume: str
    descriere: str
    numar_link: int
    slug: str
    link: str | None = None

    @model_validator(mode="after")
    def cleanup_data(self):
        self.link = (
            f"https://legislatie.just.ro/Public/DetaliiDocument/{self.numar_link}"
        )
        self.nume = unidecode(self.nume.strip()).upper()
        self.descriere = unidecode(self.descriere.strip())
        return self


class SursaConsolidata(BaseModel):
    numar_link: int
    html_name: str
    data_consolidarii: date
    link: str | None = None

    @model_validator(mode="after")
    def cleanup_data(self):
        self.link = (
            f"https://legislatie.just.ro/Public/DetaliiDocument/{self.numar_link}"
        )
        return self


class SurseConsolidate(BaseModel):
    nume: str
    descriere: str
    slug: str
    consolidari: list[SursaConsolidata]


def resolve_sources_path(path: str | Path | None = None) -> Path:
    """Resolve the sources file.

    Priority:
    1. explicit path argument
    2. ``SCRAPPER_SOURCES_FILE`` environment variable
    3. ``sources.toml`` in the current working directory
    4. the ``sources.toml`` packaged with the CLI
    """
    if path:
        return Path(path).expanduser()

    env_path = os.getenv("SCRAPPER_SOURCES_FILE")
    if env_path:
        return Path(env_path).expanduser()

    cwd_path = Path.cwd() / "sources.toml"
    if cwd_path.is_file():
        return cwd_path

    return SOURCES_TOML_PATH


def load_sources(path: str | Path | None = None) -> list[Sursa]:
    """Load and validate the source definitions from a TOML file."""
    sources_path = resolve_sources_path(path)

    try:
        with sources_path.open("rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError as ex:
        raise FileNotFoundError(
            f"Sources file not found: {sources_path}"
        ) from ex
    except tomllib.TOMLDecodeError as ex:
        raise ValueError(f"Invalid TOML in {sources_path}: {ex}") from ex

    raw_sources = data.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ValueError(
            f"{sources_path} must contain at least one [[sources]] entry."
        )

    try:
        surse = [Sursa.model_validate(item) for item in raw_sources]
    except Exception as ex:
        raise ValueError(f"Invalid source entry in {sources_path}: {ex}") from ex

    slugs = [s.slug for s in surse]
    if len(slugs) != len(set(slugs)):
        duplicates = sorted({slug for slug in slugs if slugs.count(slug) > 1})
        raise ValueError(
            f"Duplicate source slug(s) in {sources_path}: {', '.join(duplicates)}"
        )

    numbers = [s.numar_link for s in surse]
    if len(numbers) != len(set(numbers)):
        duplicates = sorted(
            {number for number in numbers if numbers.count(number) > 1}
        )
        raise ValueError(
            f"Duplicate source numar_link(s) in {sources_path}: "
            f"{', '.join(str(number) for number in duplicates)}"
        )

    return surse


# Kept as a module-level list so the rest of the pipeline can use the same shape
# as before: a list[Sursa] loaded from sources.toml.
surse_legislatie_just = load_sources()
