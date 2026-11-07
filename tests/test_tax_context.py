import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scrapper_legislatie_just import mcp_server
from scrapper_legislatie_just.cli import app
from scrapper_legislatie_just.mcp_server import handle
from scrapper_legislatie_just.tax_context import (
    TAX_CONTEXT_FILENAME,
    TAX_CONTEXT_WARNING,
    TaxContextError,
    format_save_result,
    format_tax_context,
    read_tax_context,
    save_tax_context,
    tax_context_path,
)


def _write_context(root: Path, text: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / TAX_CONTEXT_FILENAME
    path.write_text(text, encoding="utf-8")
    return path


def test_tax_context_path_uses_date_context(tmp_path):
    assert tax_context_path(tmp_path) == tmp_path / TAX_CONTEXT_FILENAME


def test_read_missing_context_is_not_an_error(tmp_path):
    root = tmp_path / "date-context"

    result = read_tax_context(root)

    assert result["exists"] is False
    assert result["valid"] is False
    assert result["data"] == {}
    assert result["error"] is None
    assert not root.exists()

    text = format_tax_context(result)
    assert "Stare: neconfigurat" in text
    assert TAX_CONTEXT_WARNING in text
    assert "Poți continua" in text
    assert "Forma juridică" in text


def test_read_valid_context(tmp_path):
    root = tmp_path / "date-context"
    _write_context(
        root,
        """\
[activitate]
forma_juridica = "PFA/II/IF"
domeniu = "Servicii IT și dezvoltare software"
caen = "6202"
regim_impozitare = "Sistem real (PFA/II/IF)"
statut_tva = "Neplătitor de TVA (regim de scutire)"
are_angajati = false
zona_activitate = "Atât în UE, cât și în afara UE"
""",
    )

    result = read_tax_context(root)

    assert result["exists"] is True
    assert result["valid"] is True
    assert result["data"]["forma_juridica"] == "PFA/II/IF"
    assert result["data"]["are_angajati"] is False
    assert result["link"].startswith("file://")

    text = format_tax_context(result)
    assert "Stare: configurat" in text
    assert "PFA/II/IF" in text
    assert "Sistem real (PFA/II/IF)" in text
    assert "Neplătitor de TVA" in text
    assert "SURSA: file://" in text


def test_read_invalid_toml_returns_clear_error(tmp_path):
    root = tmp_path / "date-context"
    _write_context(root, 'forma_juridica = "PFA\n')

    result = read_tax_context(root)

    assert result["exists"] is True
    assert result["valid"] is False
    assert "TOML invalid" in result["error"]

    text = format_tax_context(result)
    assert "Stare: invalid" in text
    assert "Eroare:" in text
    assert "legislației generale" in text


def test_read_wrong_activity_table_returns_clear_error(tmp_path):
    root = tmp_path / "date-context"
    _write_context(root, 'activitate = "nu este tabel"\n')

    result = read_tax_context(root)

    assert result["exists"] is True
    assert result["valid"] is False
    assert "tabel TOML" in result["error"]


def test_save_creates_folder_file_and_returns_link(tmp_path):
    root = tmp_path / "date-context"
    assert not root.exists()

    saved = save_tax_context(
        {
            "forma_juridica": "PFA/II/IF",
            "domeniu": "Servicii IT",
            "caen": "6202",
            "regim_impozitare": "Sistem real (PFA/II/IF)",
            "statut_tva": "Neplătitor de TVA (regim de scutire)",
            "are_angajati": False,
            "zona_activitate": "Exclusiv în România",
        },
        root,
    )

    assert root.is_dir()
    path = root / TAX_CONTEXT_FILENAME
    assert path.is_file()
    assert saved["created"] is True
    assert saved["link"] == path.resolve().as_uri()
    assert saved["updated"] == [
        "are_angajati",
        "caen",
        "domeniu",
        "forma_juridica",
        "regim_impozitare",
        "statut_tva",
        "zona_activitate",
    ]

    document = tomllib.loads(path.read_text(encoding="utf-8"))
    assert document["activitate"]["forma_juridica"] == "PFA/II/IF"
    assert document["activitate"]["are_angajati"] is False

    text = format_save_result(saved)
    assert "Stare: fișier creat" in text
    assert "SURSA: file://" in text


def test_save_merges_and_preserves_unmodified_fields(tmp_path):
    root = tmp_path / "date-context"
    _write_context(
        root,
        """\
[activitate]
domeniu = "Servicii IT"
caen = "6202"
camp_viitor = "pastrat"
""",
    )

    save_tax_context({"statut_tva": "Plătitor de TVA (perioadă lunară)"}, root)

    raw = tomllib.loads((root / TAX_CONTEXT_FILENAME).read_text(encoding="utf-8"))
    assert raw["activitate"]["caen"] == "6202"

    result = read_tax_context(root)
    data = result["data"]
    assert data["domeniu"] == "Servicii IT"
    assert data["caen"] == "6202"
    assert data["camp_viitor"] == "pastrat"
    assert data["statut_tva"] == "Plătitor de TVA (perioadă lunară)"


def test_save_preserves_other_top_level_tables(tmp_path):
    root = tmp_path / "date-context"
    _write_context(
        root,
        """\
[activitate]
domeniu = "Servicii IT"

[altele]
nota = "pastrat"
""",
    )

    save_tax_context({"statut_tva": "Neplătitor de TVA (regim de scutire)"}, root)

    raw = tomllib.loads((root / TAX_CONTEXT_FILENAME).read_text(encoding="utf-8"))
    assert raw["altele"]["nota"] == "pastrat"
    assert raw["activitate"]["statut_tva"] == "Neplătitor de TVA (regim de scutire)"


def test_save_coerces_known_field_types(tmp_path):
    root = tmp_path / "date-context"

    save_tax_context(
        {
            "forma_juridica": "PFA/II/IF",
            "domeniu": "Consultanță",
            "caen": 6202,
            "caen_secundare": "6201, 6209",
            "regim_impozitare": "Sistem real (PFA/II/IF)",
            "statut_tva": "Neplătitor de TVA, înregistrat în VIES",
            "are_angajati": "nu",
            "zona_activitate": "Atât în UE, cât și în afara UE",
            "venit_brut_anual_estimat_ron": "120000",
        },
        root,
    )

    data = read_tax_context(root)["data"]
    assert data["caen"] == "6202"
    assert data["caen_secundare"] == ["6201", "6209"]
    assert data["are_angajati"] is False
    assert data["venit_brut_anual_estimat_ron"] == 120000


def test_save_without_merge_replaces_activity_fields(tmp_path):
    root = tmp_path / "date-context"
    save_tax_context(
        {
            "domeniu": "Servicii IT",
            "statut_tva": "Neplătitor de TVA (regim de scutire)",
        },
        root,
    )

    save_tax_context({"forma_juridica": "SRL"}, root, merge=False)

    data = read_tax_context(root)["data"]
    assert data == {"forma_juridica": "SRL"}


def test_save_refuses_to_overwrite_invalid_file(tmp_path):
    root = tmp_path / "date-context"
    path = _write_context(root, 'forma_juridica = "PFA\n')

    with pytest.raises(TaxContextError, match="invalid"):
        save_tax_context({"forma_juridica": "SRL"}, root)

    assert path.read_text(encoding="utf-8") == 'forma_juridica = "PFA\n'


def test_mcp_lists_tax_context_tools():
    response = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    names = {tool["name"] for tool in response["result"]["tools"]}
    assert "get_user_tax_context" in names
    assert "save_user_tax_context" in names


def _mcp_call(name, arguments):
    response = handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )
    assert response is not None
    return response["result"]


def test_mcp_get_missing_context_does_not_block(tmp_path, monkeypatch):
    def fail_index():
        raise AssertionError("get_user_tax_context must not open the legal index")

    monkeypatch.setattr(mcp_server, "_index", fail_index)

    result = _mcp_call(
        "get_user_tax_context", {"context_dir": str(tmp_path / "date-context")}
    )

    assert result["isError"] is False
    text = result["content"][0]["text"]
    assert "Stare: neconfigurat" in text
    assert TAX_CONTEXT_WARNING in text
    assert "Poți continua" in text


def test_mcp_get_valid_context(tmp_path):
    root = tmp_path / "date-context"
    _write_context(
        root,
        """\
[activitate]
forma_juridica = "PFA/II/IF"
statut_tva = "Neplătitor de TVA (regim de scutire)"
""",
    )

    result = _mcp_call("get_user_tax_context", {"context_dir": str(root)})

    assert result["isError"] is False
    text = result["content"][0]["text"]
    assert "Stare: configurat" in text
    assert "PFA/II/IF" in text
    assert "SURSA: file://" in text


def test_mcp_get_invalid_context_returns_error_without_blocking(tmp_path):
    root = tmp_path / "date-context"
    _write_context(root, 'forma_juridica = "PFA\n')

    result = _mcp_call("get_user_tax_context", {"context_dir": str(root)})

    assert result["isError"] is False
    text = result["content"][0]["text"]
    assert "Stare: invalid" in text
    assert "TOML invalid" in text
    assert "legislației generale" in text


def test_mcp_save_and_read_context(tmp_path):
    root = tmp_path / "date-context"

    saved = _mcp_call(
        "save_user_tax_context",
        {
            "context_dir": str(root),
            "updates": {
                "forma_juridica": "SRL",
                "regim_impozitare": "Microîntreprindere (SRL)",
                "statut_tva": "Plătitor de TVA (perioadă trimestrială)",
                "are_angajati": True,
            },
        },
    )

    assert saved["isError"] is False
    assert "Context fiscal salvat" in saved["content"][0]["text"]
    assert (root / TAX_CONTEXT_FILENAME).is_file()

    read = _mcp_call("get_user_tax_context", {"context_dir": str(root)})
    text = read["content"][0]["text"]
    assert "SRL" in text
    assert "Microîntreprindere (SRL)" in text
    assert "Plătitor de TVA (perioadă trimestrială)" in text
    assert "| Are angajați | Da |" in text


def test_mcp_save_invalid_context_reports_error(tmp_path):
    root = tmp_path / "date-context"
    _write_context(root, 'forma_juridica = "PFA\n')

    result = _mcp_call(
        "save_user_tax_context",
        {"context_dir": str(root), "updates": {"forma_juridica": "SRL"}},
    )

    assert result["isError"] is True
    assert "invalid" in result["content"][0]["text"]


def test_cli_tax_context_missing_and_configured(tmp_path):
    root = tmp_path / "date-context"
    runner = CliRunner()

    missing = runner.invoke(app, ["tax-context", "--context-dir", str(root)])
    assert missing.exit_code == 0, missing.stdout
    assert "Stare: neconfigurat" in missing.stdout
    assert TAX_CONTEXT_WARNING in missing.stdout

    save_tax_context(
        {
            "forma_juridica": "SRL",
            "statut_tva": "Neplătitor de TVA (regim de scutire)",
        },
        root,
    )
    configured = runner.invoke(app, ["tax-context", "--context-dir", str(root)])
    assert configured.exit_code == 0, configured.stdout
    assert "Stare: configurat" in configured.stdout
    assert "SRL" in configured.stdout
    assert "Neplătitor de TVA" in configured.stdout
