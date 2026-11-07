"""Convert foreign-currency amounts to RON with BNR reference rates.

Thin wrapper over the ``cursvalutarbnr`` package. The package downloads the
yearly BNR XML (``https://curs.bnr.ro/files/xml/years/nbrfxrates<year>.xml``)
and keeps it in a local disk cache, so repeated conversions work offline.

The conversion is intentionally identical to ``cursvalutarbnr.ron_exchange_rate``:
the BNR rate is multiplied with the amount and rounded to four decimals with
``ROUND_HALF_UP``. When the requested date has no published rate (weekends,
holidays, future dates), the closest published rate is used and reported
explicitly, because the effective date matters for invoices and accounting.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

#: Reference XML used by BNR for the daily rates.
BNR_XML_URL = "https://curs.bnr.ro/nbrfxrates.xml"

#: BNR publishes rates with four decimals; conversions use the same precision.
RATE_QUANTUM = Decimal("0.0001")

_RON = "RON"


def _bnr():
    """Return the ``cursvalutarbnr`` module or a clear installation error."""
    try:
        import cursvalutarbnr
    except ImportError as ex:  # pragma: no cover - depends on the environment
        raise RuntimeError(
            "Libraria 'cursvalutarbnr' nu este instalata. "
            "Ruleaza 'uv sync' sau 'pip install cursvalutarbnr'."
        ) from ex
    return cursvalutarbnr


def supported_currencies() -> list[str]:
    """Return the foreign currencies for which BNR publishes rates."""
    return sorted(code for code in _bnr().Currency.values() if code != _RON)


def normalize_currency(value: Any) -> str:
    """Validate an ISO currency code and return it upper-cased."""
    code = str(value).strip().upper()
    if code == _RON:
        raise ValueError("Suma este deja in RON; alege o valuta straina.")
    if code not in _bnr().Currency.values():
        codes = ", ".join(supported_currencies())
        raise ValueError(
            f"Valuta '{value}' nu este suportata. Valute acceptate: {codes}."
        )
    return code


def _parse_amount(value: Any) -> Decimal:
    try:
        amount = Decimal(str(value).strip())
    except (AttributeError, InvalidOperation, ValueError) as ex:
        raise ValueError("Suma introdusa nu este un numar valid.") from ex
    if not amount.is_finite():
        raise ValueError("Suma introdusa trebuie sa fie un numar finit.")
    return amount


def _parse_requested_date(value: Any) -> Date:
    """Parse ``YYYY-MM-DD`` and clamp future dates like the library does."""
    bnr = _bnr()
    if value is None or (isinstance(value, str) and not value.strip()):
        return bnr.format_date(None)
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, Date):
        return bnr.format_date(value)

    text = str(value).strip()
    try:
        parsed = Date.fromisoformat(text)
    except ValueError as ex:
        raise ValueError(
            f"Data '{value}' nu este valida. Foloseste formatul YYYY-MM-DD."
        ) from ex
    return bnr.format_date(parsed)


def _pick_rate_date(available: list[Date], requested: Date) -> Date:
    """Mirror the date selection from ``cursvalutarbnr.ron_exchange_rate``.

    BNR does not publish rates on weekends and holidays, and the library picks
    the closest published date. For 1-2 January it looks at the previous year,
    because the first rates of the year appear later.
    """
    ordered = sorted(available)
    candidates = ordered
    previous_year = ordered[0].year
    if requested in (Date(requested.year, 1, 1), Date(requested.year, 1, 2)):
        in_previous_year = [day for day in ordered if day.year == previous_year]
        if in_previous_year:
            candidates = in_previous_year
    return min(candidates, key=lambda day: (abs((day - requested).days), day))


def _format_number(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _format_rate(value: Decimal) -> str:
    return format(value.quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP), "f")


def convert_to_ron(
    amount: Any,
    currency: str,
    date: str | Date | datetime | None = None,
) -> dict[str, Any]:
    """Convert ``amount`` from ``currency`` to RON using the BNR rate.

    ``date`` accepts ``YYYY-MM-DD``, a :class:`datetime.date`, a
    :class:`datetime.datetime`, or ``None`` (the library default: yesterday).
    The returned dictionary contains the amount, the applied rate (1 unit of
    currency in RON), the converted amount and the effective rate date.
    """
    bnr = _bnr()
    code = normalize_currency(currency)
    value = _parse_amount(amount)
    requested = _parse_requested_date(date)

    try:
        raw_rates = bnr.get_bnr_rates(requested)
    except Exception as ex:  # noqa: BLE001 - network/XML errors need context
        raise RuntimeError(f"Nu am putut obtine cursul BNR: {ex}") from ex

    rates: dict[Date, dict[str, float]] = {}
    for key, day_rates in raw_rates.items():
        try:
            rates[Date.fromisoformat(str(key))] = day_rates
        except (TypeError, ValueError):
            continue
    if not rates:
        raise RuntimeError("BNR nu a returnat niciun curs valutar.")

    rate_date = _pick_rate_date(list(rates), requested)
    day_rates = rates[rate_date]
    if code not in day_rates:
        raise ValueError(
            f"BNR nu a publicat curs pentru {code} la data {rate_date.isoformat()}."
        )

    rate = Decimal(str(day_rates[code]))
    converted = (value * rate).quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)
    return {
        "amount": value,
        "currency": code,
        "rate": rate,
        "ron": converted,
        "requested_date": requested,
        "rate_date": rate_date,
        "source": BNR_XML_URL,
    }


def format_conversion(result: dict[str, Any]) -> str:
    """Render a :func:`convert_to_ron` result as readable text."""
    lines = [
        f"{_format_number(result['amount'])} {result['currency']} = "
        f"{_format_number(result['ron'])} RON",
        f"Curs BNR: 1 {result['currency']} = {_format_rate(result['rate'])} RON",
        f"Data cursului: {result['rate_date'].isoformat()}",
    ]

    requested = result["requested_date"]
    rate_date = result["rate_date"]
    if rate_date != requested:
        lines.append(
            f"Atentie: pentru {requested.isoformat()} nu exista curs BNR "
            f"publicat (weekend, sarbatoare sau data viitoare); s-a folosit "
            f"cursul cel mai apropiat, din {rate_date.isoformat()}."
        )

    lines.append(f"Sursa: BNR, {result['source']}")
    return "\n".join(lines)
