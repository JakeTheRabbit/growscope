"""Recipes: weekly setpoint curves anchored to flip.

Shape of recipe params:
    {"Air temp": {"unit": "C", "weeks": {"-2": 26, "-1": 26, "1": 25, "2": 25, ...}},
     "VPD": {"unit": "kPa", "weeks": {...}}}

Week numbers are relative to flip: week 1 = first flower week, week -1 = last
veg week. A grow without a flip date anchors week 1 to its start date instead.
"""
from __future__ import annotations

from datetime import date, timedelta

from . import db


def _flip(grow: dict) -> date:
    anchor = grow.get("flip_date") or grow["start_date"]
    return date.fromisoformat(anchor)


def _week_of(day: date, flip: date) -> int:
    delta = (day - flip).days
    if delta >= 0:
        return delta // 7 + 1
    return -((-delta - 1) // 7 + 1)


def evaluate(grow: dict, start: date, end: date) -> dict:
    """Target step-series per parameter across [start, end]:
    {param: {"unit": u, "points": [[iso_date, value], ...]}} - one point per day,
    only where the recipe defines that week."""
    if not grow.get("recipe_id"):
        return {}
    recipe = db.recipe(grow["recipe_id"])
    if not recipe:
        return {}
    flip = _flip(grow)
    out: dict = {}
    for param, spec in recipe["params"].items():
        weeks = {int(k): v for k, v in spec.get("weeks", {}).items() if v not in ("", None)}
        if not weeks:
            continue
        points = []
        day = start
        while day <= end:
            value = weeks.get(_week_of(day, flip))
            if value is not None:
                points.append([day.isoformat(), value])
            day += timedelta(days=1)
        if points:
            out[param] = {"unit": spec.get("unit", ""), "points": points}
    return out


def grow_window(grow: dict) -> tuple[date, date]:
    start = date.fromisoformat(grow["start_date"])
    end = date.fromisoformat(grow["chop_date"]) if grow.get("chop_date") else date.today()
    return start, end
