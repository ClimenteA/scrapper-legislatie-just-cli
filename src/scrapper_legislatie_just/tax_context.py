"""Persistent fiscal context supplied by the user.

The assistant personalises fiscal answers from a small TOML file:

    date-context/informatii-activitate.toml

The file is optional. When it is missing the MCP tool returns a clear
"neconfigurat" result plus the questionnaire, and the assistant may continue
with the general legal answer. When the file is invalid the tool reports the
parse error and the assistant continues without blocking.

Python 3.11 ships ``tomllib`` for reading. Writing uses a tiny serialiser so the
project does not need an extra dependency. The ``[activitate]`` table is merged
on save, which keeps unknown fields added by future versions.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from pathlib import Path
from typing import Any

from .config import resolve_context_dir

TAX_CONTEXT_FILENAME = "informatii-activitate.toml"
ACTIVITY_TABLE = "activitate"

#: Shown verbatim when the user declines the optional questionnaire.
TAX_CONTEXT_WARNING = (
    "Nu ai configurat încă informațiile despre activitatea ta. Răspunsurile "
    "fiscale pot diferi în funcție de forma juridică (PFA/SRL), regimul de "
    "impozitare, TVA și alte caracteristici. Răspunsul va fi formulat pe baza "
    "informațiilor generale disponibile."
)

#: Conversational questionnaire. Only fields needed to personalise answers;
#: no CNP, personal address, IBAN or other sensitive data.
QUESTIONNAIRE = """\
1. Forma juridică: SRL, PFA/II/IF, persoană fizică sau altă formă.
2. Domeniul de activitate / tipul activității desfășurate.
3. Cod CAEN principal (opțional: coduri CAEN secundare).
4. Regimul de impozitare: microîntreprindere (SRL), impozit pe profit (SRL),
   normă de venit (PFA/II/IF), sistem real (PFA/II/IF) sau alt regim.
5. Statut TVA: neplătitor de TVA (regim de scutire), plătitor lunar, plătitor
   trimestrial sau neplătitor înregistrat în VIES.
6. Are angajați? Da/Nu (contract de muncă activ).
7. Zona de activitate: exclusiv România, exclusiv UE, exclusiv în afara UE sau
   atât în UE, cât și în afara UE.
8. Opțional: nume firmă, CUI, cod TVA, venit brut anual estimat în RON, alte
   detalii relevante fiscal."""


class TaxContextError(ValueError):
    """Raised when the tax context cannot be read or written safely."""


_FIELD_LABELS = {
    "forma_juridica": "Formă juridică",
    "domeniu": "Domeniu de activitate",
    "caen": "Cod CAEN principal",
    "caen_secundare": "Coduri CAEN secundare",
    "regim_impozitare": "Regim de impozitare",
    "statut_tva": "Statut TVA",
    "are_angajati": "Are angajați",
    "zona_activitate": "Zona de activitate",
    "nume_firma": "Nume firmă",
    "cui": "CUI",
    "cod_tva": "Cod TVA",
    "venit_brut_anual_estimat_ron": "Venit brut anual estimat (RON)",
    "detalii_activitate": "Alte detalii relevante",
}

_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def tax_context_path(context_dir: str | Path | None = None) -> Path:
    """Return ``<date-context>/informatii-activitate.toml``."""
    return resolve_context_dir(context_dir) / TAX_CONTEXT_FILENAME


def _file_uri(path: Path) -> str:
    return path.resolve().as_uri()


def _toml_key(key: str) -> str:
    return key if _BARE_KEY_RE.match(key) else json.dumps(key, ensure_ascii=False)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        items = [_toml_value(item) for item in value if item is not None]
        return "[" + ", ".join(items) + "]"
    if isinstance(value, dict):
        items = [
            f"{_toml_key(key)} = {_toml_value(item)}"
            for key, item in value.items()
            if item is not None
        ]
        return "{ " + ", ".join(items) + " }"
    raise TypeError(f"Tip TOML nesuportat: {type(value).__name__}")


def _dump_table(prefix: str, table: dict[str, Any]) -> list[str]:
    lines = [f"[{prefix}]"]
    nested: list[tuple[str, dict[str, Any]]] = []
    for key, value in table.items():
        if value is None:
            continue
        if isinstance(value, dict):
            nested.append((key, value))
        else:
            lines.append(f"{_toml_key(key)} = {_toml_value(value)}")
    for key, value in nested:
        lines.append("")
        lines.extend(_dump_table(f"{prefix}.{_toml_key(key)}", value))
    return lines


def dump_toml(document: dict[str, Any]) -> str:
    """Serialise the small TOML subset used by the fiscal context."""
    lines: list[str] = []
    tables: list[tuple[str, dict[str, Any]]] = []
    for key, value in document.items():
        if value is None:
            continue
        if isinstance(value, dict):
            tables.append((key, value))
        else:
            lines.append(f"{_toml_key(key)} = {_toml_value(value)}")
    for key, table in tables:
        if lines:
            lines.append("")
        lines.extend(_dump_table(_toml_key(key), table))
    return "\n".join(lines).rstrip() + "\n"


def _coerce_bool(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        folded = value.strip().casefold()
        if folded in {"da", "true", "yes"}:
            return True
        if folded in {"nu", "false", "no"}:
            return False
    return value


def _caen_code(value: Any) -> str:
    text = str(value).strip()
    if text.isdigit():
        return text.zfill(4)
    return text


def _coerce_activity(updates: dict[str, Any]) -> dict[str, Any]:
    """Apply only the type fixes needed for a valid, readable TOML file."""
    coerced = dict(updates)

    if "caen" in coerced and coerced["caen"] is not None:
        coerced["caen"] = _caen_code(coerced["caen"])
    if "caen_secundare" in coerced:
        value = coerced["caen_secundare"]
        if isinstance(value, str):
            parts = [part.strip() for part in re.split(r"[,;]", value) if part.strip()]
            coerced["caen_secundare"] = [_caen_code(part) for part in parts]
        elif isinstance(value, (list, tuple)):
            coerced["caen_secundare"] = [_caen_code(part) for part in value]
        elif isinstance(value, int) and not isinstance(value, bool):
            coerced["caen_secundare"] = [_caen_code(value)]

    if "are_angajati" in coerced:
        coerced["are_angajati"] = _coerce_bool(coerced["are_angajati"])

    if "venit_brut_anual_estimat_ron" in coerced:
        value = coerced["venit_brut_anual_estimat_ron"]
        if isinstance(value, float) and value.is_integer():
            coerced["venit_brut_anual_estimat_ron"] = int(value)
        elif isinstance(value, str) and value.strip().isdigit():
            coerced["venit_brut_anual_estimat_ron"] = int(value.strip())

    for key in ("domeniu", "nume_firma", "cui", "cod_tva", "detalii_activitate"):
        if key in coerced and coerced[key] is not None:
            coerced[key] = str(coerced[key])

    return coerced


def read_tax_context(context_dir: str | Path | None = None) -> dict[str, Any]:
    """Read the TOML file and return a structured, non-blocking result."""
    path = tax_context_path(context_dir)
    result: dict[str, Any] = {
        "exists": path.is_file(),
        "valid": False,
        "path": str(path),
        "link": _file_uri(path),
        "data": {},
        "document": {},
        "error": None,
    }
    if not result["exists"]:
        return result

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as ex:
        result["error"] = f"Fișierul nu poate fi citit: {ex}"
        return result

    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as ex:
        result["error"] = f"TOML invalid: {ex}"
        return result

    activity = document.get(ACTIVITY_TABLE)
    if activity is not None and not isinstance(activity, dict):
        result["error"] = (
            f"Secțiunea [{ACTIVITY_TABLE}] trebuie să fie un tabel TOML."
        )
        return result

    result["valid"] = True
    result["document"] = document
    result["data"] = activity or {}
    return result


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "Da" if value else "Nu"
    if isinstance(value, (list, tuple)):
        return ", ".join(_format_value(item) for item in value) or "(gol)"
    if value is None or value == "":
        return "(nesetat)"
    return str(value)


def _format_table(data: dict[str, Any]) -> list[str]:
    lines = ["| câmp | valoare |", "|---|---|"]
    for key, value in data.items():
        label = _FIELD_LABELS.get(key, key)
        lines.append(f"| {label} | {_format_value(value)} |")
    return lines


def format_tax_context(result: dict[str, Any]) -> str:
    """Render the read result for the assistant (markdown, no exceptions)."""
    path = result["path"]
    if not result["exists"]:
        return "\n".join(
            [
                "# Context fiscal utilizator",
                "Stare: neconfigurat",
                f"Fișier: {path}",
                "",
                TAX_CONTEXT_WARNING,
                "",
                "Poți continua cu întrebarea legislativă fără să completezi "
                "chestionarul.",
                "",
                "Dacă vrei să îl completezi acum, răspunde pe scurt la:",
                "",
                QUESTIONNAIRE,
                "",
                "După confirmare, informațiile se salvează cu tool-ul "
                "`save_user_tax_context`.",
            ]
        )

    if not result["valid"]:
        return "\n".join(
            [
                "# Context fiscal utilizator",
                "Stare: invalid",
                f"Fișier: {path}",
                f"Eroare: {result['error']}",
                "",
                "Fișierul există, dar nu poate fi citit. Răspunsul poate "
                "continua pe baza legislației generale; corectează fișierul "
                "sau cere actualizarea lui.",
            ]
        )

    if not result["data"]:
        return "\n".join(
            [
                "# Context fiscal utilizator",
                "Stare: fișier gol",
                f"Fișier: {path}",
                f"SURSA: {result['link']}",
                "",
                f"Fișierul există, dar nu conține secțiunea [{ACTIVITY_TABLE}].",
                "Poți continua cu răspunsul general sau propune completarea "
                "chestionarului.",
            ]
        )

    lines = [
        "# Context fiscal utilizator",
        "Stare: configurat",
        f"Fișier: {path}",
        f"SURSA: {result['link']}",
        "",
        "Folosește aceste date doar pentru a personaliza răspunsul. Nu "
        "presupune câmpuri care lipsesc și nu modifica întrebarea originală.",
        "",
        *_format_table(result["data"]),
    ]
    return "\n".join(lines)


def save_tax_context(
    updates: dict[str, Any],
    context_dir: str | Path | None = None,
    *,
    merge: bool = True,
) -> dict[str, Any]:
    """Create or update the TOML file, preserving fields not being changed."""
    if not isinstance(updates, dict) or not updates:
        raise TaxContextError("Trimite cel puțin un câmp de actualizat.")

    if set(updates) == {ACTIVITY_TABLE} and isinstance(updates[ACTIVITY_TABLE], dict):
        updates = updates[ACTIVITY_TABLE]

    path = tax_context_path(context_dir)
    created = not path.is_file()
    document: dict[str, Any] = {}
    activity: dict[str, Any] = {}

    if path.is_file():
        existing = read_tax_context(context_dir)
        if not existing["valid"]:
            raise TaxContextError(
                f"Fișierul {path} există, dar este invalid ({existing['error']}). "
                "Corectează-l sau șterge-l înainte de actualizare."
            )
        document = existing["document"]
        activity = document.get(ACTIVITY_TABLE) or {}
        if not isinstance(activity, dict):
            raise TaxContextError(
                f"Secțiunea [{ACTIVITY_TABLE}] din {path} nu este un tabel TOML."
            )

    coerced = _coerce_activity(updates)
    if not coerced:
        raise TaxContextError("După pregătire nu a rămas niciun câmp de salvat.")

    if merge:
        activity = {**activity, **coerced}
    else:
        activity = coerced
    document[ACTIVITY_TABLE] = activity

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(dump_toml(document), encoding="utf-8")
        os.replace(temporary, path)
    except OSError as ex:
        raise TaxContextError(f"Fișierul {path} nu poate fi scris: {ex}") from ex

    verified = read_tax_context(context_dir)
    if not verified["valid"]:
        raise TaxContextError(
            f"Fișierul {path} a fost scris, dar nu poate fi recitit: "
            f"{verified['error']}"
        )
    for key, value in coerced.items():
        if verified["data"].get(key) != value:
            raise TaxContextError(
                f"Verificarea a eșuat pentru câmpul {key!r} din {path}."
            )

    return {
        "path": str(path),
        "link": _file_uri(path),
        "created": created,
        "updated": sorted(coerced),
        "data": verified["data"],
    }


def format_save_result(result: dict[str, Any]) -> str:
    """Render the save confirmation for the assistant."""
    action = "creat" if result["created"] else "actualizat"
    return "\n".join(
        [
            "# Context fiscal salvat",
            f"Stare: fișier {action}",
            f"Fișier: {result['path']}",
            f"SURSA: {result['link']}",
            f"Câmpuri salvate: {', '.join(result['updated'])}",
        ]
    )
