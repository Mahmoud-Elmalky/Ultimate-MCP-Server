"""Deterministic unit conversion utility (`convert_units`).

Supports length, mass/weight, temperature, volume, speed, area, time, digital storage,
and pressure without external dependencies.
"""

from __future__ import annotations

import math

from ultimate_mcp.core.models import ErrorCode, ToolResult, UltimateMCPError

# Base units per category:
# length -> meter (m)
# mass -> kilogram (kg)
# time -> second (s)
# volume -> liter (l)
# speed -> meter per second (m/s)
# area -> square meter (m2)
# data -> byte (b)
# pressure -> pascal (pa)
LINEAR_UNITS: dict[str, tuple[str, float]] = {
    # Length (base: m)
    "mm": ("length", 0.001),
    "millimeter": ("length", 0.001),
    "millimeters": ("length", 0.001),
    "cm": ("length", 0.01),
    "centimeter": ("length", 0.01),
    "centimeters": ("length", 0.01),
    "m": ("length", 1.0),
    "meter": ("length", 1.0),
    "meters": ("length", 1.0),
    "km": ("length", 1000.0),
    "kilometer": ("length", 1000.0),
    "kilometers": ("length", 1000.0),
    "in": ("length", 0.0254),
    "inch": ("length", 0.0254),
    "inches": ("length", 0.0254),
    "ft": ("length", 0.3048),
    "foot": ("length", 0.3048),
    "feet": ("length", 0.3048),
    "yd": ("length", 0.9144),
    "yard": ("length", 0.9144),
    "yards": ("length", 0.9144),
    "mi": ("length", 1609.344),
    "mile": ("length", 1609.344),
    "miles": ("length", 1609.344),
    "nmi": ("length", 1852.0),
    # Mass (base: kg)
    "mg": ("mass", 1e-6),
    "milligram": ("mass", 1e-6),
    "milligrams": ("mass", 1e-6),
    "g": ("mass", 0.001),
    "gram": ("mass", 0.001),
    "grams": ("mass", 0.001),
    "kg": ("mass", 1.0),
    "kilogram": ("mass", 1.0),
    "kilograms": ("mass", 1.0),
    "t": ("mass", 1000.0),
    "tonne": ("mass", 1000.0),
    "metric_ton": ("mass", 1000.0),
    "oz": ("mass", 0.028349523125),
    "ounce": ("mass", 0.028349523125),
    "ounces": ("mass", 0.028349523125),
    "lb": ("mass", 0.45359237),
    "lbs": ("mass", 0.45359237),
    "pound": ("mass", 0.45359237),
    "pounds": ("mass", 0.45359237),
    # Time (base: s)
    "ms": ("time", 0.001),
    "millisecond": ("time", 0.001),
    "milliseconds": ("time", 0.001),
    "s": ("time", 1.0),
    "sec": ("time", 1.0),
    "second": ("time", 1.0),
    "seconds": ("time", 1.0),
    "min": ("time", 60.0),
    "minute": ("time", 60.0),
    "minutes": ("time", 60.0),
    "h": ("time", 3600.0),
    "hr": ("time", 3600.0),
    "hour": ("time", 3600.0),
    "hours": ("time", 3600.0),
    "d": ("time", 86400.0),
    "day": ("time", 86400.0),
    "days": ("time", 86400.0),
    "wk": ("time", 604800.0),
    "week": ("time", 604800.0),
    "weeks": ("time", 604800.0),
    # Volume (base: l)
    "ml": ("volume", 0.001),
    "milliliter": ("volume", 0.001),
    "milliliters": ("volume", 0.001),
    "l": ("volume", 1.0),
    "liter": ("volume", 1.0),
    "liters": ("volume", 1.0),
    "m3": ("volume", 1000.0),
    "gal": ("volume", 3.785411784),
    "gallon": ("volume", 3.785411784),
    "gallons": ("volume", 3.785411784),
    "qt": ("volume", 0.946352946),
    "pt": ("volume", 0.473176473),
    "cup": ("volume", 0.2365882365),
    "fl_oz": ("volume", 0.0295735295625),
    # Speed (base: m/s)
    "m/s": ("speed", 1.0),
    "mps": ("speed", 1.0),
    "km/h": ("speed", 1.0 / 3.6),
    "kph": ("speed", 1.0 / 3.6),
    "kmh": ("speed", 1.0 / 3.6),
    "mph": ("speed", 0.44704),
    "kn": ("speed", 1852.0 / 3600.0),
    "knot": ("speed", 1852.0 / 3600.0),
    "knots": ("speed", 1852.0 / 3600.0),
    # Area (base: m2)
    "cm2": ("area", 0.0001),
    "m2": ("area", 1.0),
    "sqm": ("area", 1.0),
    "km2": ("area", 1_000_000.0),
    "ha": ("area", 10_000.0),
    "hectare": ("area", 10_000.0),
    "sqft": ("area", 0.09290304),
    "ft2": ("area", 0.09290304),
    "acre": ("area", 4046.8564224),
    "acres": ("area", 4046.8564224),
    # Digital storage (base: byte)
    "b": ("data", 1.0),
    "byte": ("data", 1.0),
    "bytes": ("data", 1.0),
    "kb": ("data", 1000.0),
    "mb": ("data", 1_000_000.0),
    "gb": ("data", 1_000_000_000.0),
    "tb": ("data", 1_000_000_000_000.0),
    "kib": ("data", 1024.0),
    "mib": ("data", 1024.0**2),
    "gib": ("data", 1024.0**3),
    "tib": ("data", 1024.0**4),
    # Pressure (base: Pa)
    "pa": ("pressure", 1.0),
    "kpa": ("pressure", 1000.0),
    "bar": ("pressure", 100_000.0),
    "atm": ("pressure", 101_325.0),
    "psi": ("pressure", 6894.757293168),
}

TEMPERATURE_ALIASES: dict[str, str] = {
    "c": "c",
    "celsius": "c",
    "degc": "c",
    "f": "f",
    "fahrenheit": "f",
    "degf": "f",
    "k": "k",
    "kelvin": "k",
}


def _convert_temperature(value: float, src: str, dst: str) -> float:
    if src == dst:
        return value
    # Convert to Celsius first
    if src == "c":
        celsius = value
    elif src == "f":
        celsius = (value - 32.0) * 5.0 / 9.0
    else:
        if value < 0:
            raise UltimateMCPError(
                ErrorCode.INVALID_INPUT,
                "Kelvin temperature cannot be negative.",
            )
        celsius = value - 273.15

    if celsius < -273.1500001:
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT,
            "Temperature is below absolute zero (-273.15 C).",
        )

    if dst == "c":
        return celsius
    if dst == "f":
        return celsius * 9.0 / 5.0 + 32.0
    return celsius + 273.15


def convert_units_value(value: float, from_unit: str, to_unit: str) -> tuple[float, str]:
    """Convert ``value`` from ``from_unit`` to ``to_unit`` and return ``(result, category)``."""
    if math.isnan(value) or math.isinf(value):
        raise UltimateMCPError(ErrorCode.INVALID_INPUT, "Value must be a finite number.")

    src_key = from_unit.strip().lower().replace(" ", "_")
    dst_key = to_unit.strip().lower().replace(" ", "_")

    if src_key in TEMPERATURE_ALIASES and dst_key in TEMPERATURE_ALIASES:
        converted = _convert_temperature(
            value,
            TEMPERATURE_ALIASES[src_key],
            TEMPERATURE_ALIASES[dst_key],
        )
        return converted, "temperature"

    src_entry = LINEAR_UNITS.get(src_key)
    dst_entry = LINEAR_UNITS.get(dst_key)
    if src_entry is None or dst_entry is None:
        unknown = from_unit if src_entry is None else to_unit
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT,
            f"Unsupported or incompatible unit '{unknown}'.",
        )

    src_cat, src_factor = src_entry
    dst_cat, dst_factor = dst_entry
    if src_cat != dst_cat:
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT,
            f"Cannot convert between incompatible categories '{src_cat}' ({from_unit}) "
            f"and '{dst_cat}' ({to_unit}).",
        )

    base_val = value * src_factor
    return base_val / dst_factor, src_cat


def convert_units_service(value: float, from_unit: str, to_unit: str) -> ToolResult:
    """Service returning a standardized ``ToolResult`` for ``convert_units``."""
    converted, category = convert_units_value(value, from_unit, to_unit)
    rounded = round(converted, 8)
    return ToolResult.ok(
        tool="convert_units",
        summary=f"{value} {from_unit.strip()} = {rounded} {to_unit.strip()} ({category})",
        data={
            "input_value": value,
            "from_unit": from_unit.strip(),
            "to_unit": to_unit.strip(),
            "category": category,
            "converted_value": rounded,
        },
    )
