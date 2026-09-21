"""Toggle-button-plus-container pattern — the Flet stand-in for Streamlit's
`st.expander`.

Used three times already (brand kit's title history, settings' advanced
options and API key guides) with the same hand-rolled shape; pulled out here
before a fourth (workbench) copy makes it worth doing.
"""
from __future__ import annotations

import flet as ft


def collapsible(label: str, content: ft.Control, initially_open: bool = False) -> list[ft.Control]:
    box = ft.Container(
        visible=initially_open,
        padding=ft.Padding.only(top=8, bottom=8),
        content=content,
    )

    def toggle(e: ft.Event) -> None:
        box.visible = not box.visible
        box.update()

    return [ft.TextButton(label, on_click=toggle), box]
