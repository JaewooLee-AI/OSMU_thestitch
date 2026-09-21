"""Reusable editable table — the Flet stand-in for Streamlit's `st.data_editor`.

Flet has no spreadsheet-like editable grid control, so this wraps an
`ft.DataTable` with per-row delete buttons and an "add row" affordance, and
exposes `get_rows()` to read current values back out as plain dicts.

Column `default` matters: a blank cell on a row the user just typed into
must resolve to that default, not to an empty/falsy value — the brand kit's
SEO keyword table depends on this (a blank weight means "no opinion yet",
i.e. neutral weight 1.0, not 0, which would silently retire the keyword;
a blank "검색 타깃" checkbox means still-included, not excluded). Checkbox
columns get this for free because a new row's control is created already
set to `default`. Number columns re-apply it at read time for a cell that
was cleared back to empty after being touched.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import flet as ft


@dataclass
class ColumnSpec:
    key: str
    label: str
    kind: str = "text"  # "text" | "number" | "checkbox"
    default: Any = ""
    width: int | None = None


class EditableTable:
    def __init__(self, columns: list[ColumnSpec], rows: list[dict] | None = None):
        self.columns = columns
        self._row_controls: list[dict[str, ft.Control]] = []
        self._table = ft.DataTable(
            columns=[ft.DataColumn(ft.Text(c.label)) for c in self.columns] + [ft.DataColumn(ft.Text(""))],
            rows=[],
        )
        for row in rows or []:
            self._add_row(row)

    def _make_cell_control(self, spec: ColumnSpec, value: Any) -> ft.Control:
        if spec.kind == "checkbox":
            return ft.Checkbox(value=bool(value) if value is not None else bool(spec.default))
        if spec.kind == "number":
            shown = "" if value in (None, "") else str(value)
            return ft.TextField(value=shown, width=spec.width or 100, dense=True)
        return ft.TextField(value="" if value is None else str(value), width=spec.width, dense=True, expand=spec.width is None)

    def _add_row(self, row: dict | None = None) -> None:
        row = row or {}
        controls = {c.key: self._make_cell_control(c, row.get(c.key)) for c in self.columns}
        self._row_controls.append(controls)

        def _remove(e: ft.Event) -> None:
            idx = self._row_controls.index(controls)
            self._row_controls.pop(idx)
            self._table.rows.pop(idx)
            self._table.update()

        cells = [ft.DataCell(controls[c.key]) for c in self.columns]
        cells.append(ft.DataCell(ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, on_click=_remove)))
        self._table.rows.append(ft.DataRow(cells=cells))

    def build(self) -> ft.Control:
        def _add(e: ft.Event) -> None:
            self._add_row()
            self._table.update()

        return ft.Column([self._table, ft.TextButton("+ 행 추가", on_click=_add)])

    def get_rows(self) -> list[dict]:
        result = []
        for controls in self._row_controls:
            row: dict[str, Any] = {}
            for spec in self.columns:
                ctrl = controls[spec.key]
                if spec.kind == "checkbox":
                    row[spec.key] = ctrl.value if ctrl.value is not None else bool(spec.default)
                elif spec.kind == "number":
                    raw = (ctrl.value or "").strip()
                    row[spec.key] = float(raw) if raw else spec.default
                else:
                    row[spec.key] = (ctrl.value or "").strip()
            result.append(row)
        return result
