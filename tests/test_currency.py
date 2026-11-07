from datetime import date
from decimal import Decimal

import cursvalutarbnr
import pytest
from typer.testing import CliRunner

from scrapper_legislatie_just import currency
from scrapper_legislatie_just.cli import app
from scrapper_legislatie_just.mcp_server import handle

RATES = {
    "2023-12-29": {"EUR": 4.9700, "USD": 4.5800},
    "2024-08-01": {"EUR": 4.9700, "USD": 4.5800},
    "2024-08-02": {"EUR": 4.9753, "USD": 4.6000},
    "2024-08-05": {"EUR": 4.9800, "USD": 4.6100},
}


@pytest.fixture
def bnr(monkeypatch):
    """Serve fixed rates instead of downloading the BNR XML."""
    monkeypatch.setattr(cursvalutarbnr, "get_bnr_rates", lambda _date: RATES)
    return RATES


def test_supported_currencies_excludes_ron():
    codes = currency.supported_currencies()
    assert "EUR" in codes
    assert "USD" in codes
    assert "RON" not in codes
    assert codes == sorted(codes)


def test_convert_to_ron_uses_bnr_rate(bnr):
    result = currency.convert_to_ron(100, "eur", "2024-08-02")

    assert result["currency"] == "EUR"
    assert result["rate"] == Decimal("4.9753")
    assert result["ron"] == Decimal("497.53")
    assert result["requested_date"] == date(2024, 8, 2)
    assert result["rate_date"] == date(2024, 8, 2)
    assert result["source"] == currency.BNR_XML_URL


def test_convert_accepts_numeric_strings_and_negative_amounts(bnr):
    result = currency.convert_to_ron("-10.5", "USD", "2024-08-02")

    assert result["ron"] == Decimal("-48.3")


def test_result_matches_library_ron_exchange_rate(bnr):
    expected = cursvalutarbnr.ron_exchange_rate(1250.50, "EUR", "2024-08-02")
    result = currency.convert_to_ron(1250.50, "EUR", "2024-08-02")

    assert float(result["ron"]) == expected


def test_missing_rate_falls_back_to_closest_date_and_says_so(bnr):
    # Sunday 2024-08-04: the library picks the closest published rate.
    result = currency.convert_to_ron(10, "EUR", "2024-08-04")
    assert result["rate_date"] == date(2024, 8, 5)

    text = currency.format_conversion(result)
    assert "10 EUR = 49.8 RON" in text
    assert "Curs BNR: 1 EUR = 4.9800 RON" in text
    assert "Data cursului: 2024-08-05" in text
    assert "2024-08-04" in text
    assert "cursul cel mai apropiat" in text


def test_first_days_of_year_use_previous_year(bnr):
    result = currency.convert_to_ron(1, "EUR", "2024-01-01")
    assert result["rate_date"] == date(2023, 12, 29)


def test_future_date_is_clamped_to_yesterday(bnr, monkeypatch):
    monkeypatch.setattr(currency, "_bnr", lambda: cursvalutarbnr)
    monkeypatch.setattr(cursvalutarbnr, "format_date", lambda _value: date(2024, 8, 5))

    result = currency.convert_to_ron(1, "EUR", "2099-01-01")
    assert result["requested_date"] == date(2024, 8, 5)


def test_invalid_currency(bnr):
    with pytest.raises(ValueError, match="nu este suportata"):
        currency.convert_to_ron(10, "XYZ")


def test_ron_is_rejected(bnr):
    with pytest.raises(ValueError, match="deja in RON"):
        currency.convert_to_ron(10, "RON")


def test_invalid_date(bnr):
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        currency.convert_to_ron(10, "EUR", "02-08-2024")


def test_invalid_amount(bnr):
    with pytest.raises(ValueError, match="numar valid"):
        currency.convert_to_ron("o suta", "EUR")


def test_mcp_convert_currency_tool(bnr):
    response = handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "convert_currency",
                "arguments": {
                    "amount": 10,
                    "currency": "USD",
                    "date": "2024-08-02",
                },
            },
        }
    )

    assert response is not None
    assert response["result"]["isError"] is False
    text = response["result"]["content"][0]["text"]
    assert "10 USD = 46 RON" in text
    assert "1 USD = 4.6000 RON" in text


def test_mcp_lists_convert_currency():
    response = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {tool["name"] for tool in response["result"]["tools"]}
    assert "convert_currency" in names


def test_mcp_lists_every_tool():
    """A stale or partial server makes the model fall back to curl."""
    response = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {tool["name"] for tool in response["result"]["tools"]}
    assert names == {
        "search",
        "article",
        "show",
        "neighbors",
        "context",
        "context_files",
        "read_context",
        "convert_currency",
        "save_answer",
        "get_user_tax_context",
        "save_user_tax_context",
        "docs",
        "cypher",
    }


def test_cli_convert(bnr):
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["convert", "100", "EUR", "--date", "2024-08-02"],
    )

    assert result.exit_code == 0, result.stdout
    assert "100 EUR = 497.53 RON" in result.stdout
    assert "Data cursului: 2024-08-02" in result.stdout


def test_cli_convert_reports_bad_currency(bnr):
    runner = CliRunner()
    result = runner.invoke(app, ["convert", "10", "XYZ"])

    assert result.exit_code == 1
    assert "nu este suportata" in result.stderr
