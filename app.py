from __future__ import annotations

import json
import math
import os
import re
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

import matplotlib.dates as mdates
import numpy as np
import pandas as pd
from matplotlib.backend_bases import MouseButton
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.widgets import RectangleSelector

from .models import (
    HISTORICAL_RANGES,
    INTRADAY_MATRIX,
    INTRADAY_RANGES,
    Instrument,
    MarketSession,
    RangeSpec,
)
from .provider_registry import provider_health_summary
from .providers import MarketDataProvider
from .sec_edgar import SecEdgarClient, format_sec_company_context


BG = "#000000"
PANEL = "#17212b"
GRID = "#30404d"
ORANGE = "#f6a400"
TEXT = "#e8edf2"
MUTED = "#a7b3be"
UP = "#38c172"
DOWN = "#ef5350"
SERIES_COLORS = (
    "#f6a400",
    "#42a5f5",
    "#66bb6a",
    "#ab47bc",
    "#ef5350",
    "#26c6da",
    "#ffee58",
    "#ec407a",
    "#8d6e63",
    "#b0bec5",
)
MAX_SERIES = 10
SEARCH_DEBOUNCE_MS = 275
SOURCE_WATCH_INTERVAL_MS = 1000
RUNTIME_SOURCE_FILES = (
    "app.py",
    "models.py",
    "providers.py",
    "provider_registry.py",
    "run.py",
    "sec_edgar.py",
)
DEFAULT_WINDOW_GEOMETRY = "1300x820"
MIN_WINDOW_WIDTH = 1040
MIN_WINDOW_HEIGHT = 650
MIN_CHART_WINDOW_WIDTH = 620
MIN_CHART_WINDOW_HEIGHT = 420


@dataclass(frozen=True)
class OlsCoefficient:
    estimate: float
    std_error: float
    t_stat: float
    p_value: float


@dataclass(frozen=True)
class BetaModelStats:
    y_symbol: str
    observations: int
    r_squared: float
    adjusted_r_squared: float
    alpha: OlsCoefficient
    betas: dict[str, OlsCoefficient]


class MarketTerminalApp(tk.Tk):
    def __init__(self, provider: MarketDataProvider | None = None) -> None:
        super().__init__()
        self.provider = provider or MarketDataProvider()
        self.sec_client = SecEdgarClient()
        self.title("Market Terminal | Price Charts")
        self.minsize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        self.configure(bg=BG)
        state_root = Path(__file__).resolve().parent / "out"
        self.window_state_path = state_root / "window_state.json"
        self.layout_state_path = state_root / "layout.json"
        self.watchlist_state_path = state_root / "watchlist.json"
        self.saved_window_state = load_window_state(self.window_state_path)
        self.saved_layout_state = load_layout_state(self.layout_state_path)
        self.startup_layout_state = json.loads(json.dumps(self.saved_layout_state))
        self.saved_watchlist_state = load_watchlist_state(self.watchlist_state_path)
        self.geometry(self.saved_window_state["geometry"])

        self.search_var = tk.StringVar(value="")
        self.watchlist_search_var = tk.StringVar(value="")
        self.mode_var = tk.StringVar(value="Intraday")
        self.status_var = tk.StringVar(
            value="Public/delayed market data via Yahoo Finance | Identifier mapping via OpenFIGI"
        )
        self.quote_var = tk.StringVar(value="Search for an asset to begin.")
        self.fundamentals_var = tk.StringVar(value="")
        self.sec_context_var = tk.StringVar(value="")
        self.identity_var = tk.StringVar(value="")
        self.measurement_var = tk.StringVar(value="")
        self.session_var = tk.StringVar(value="")
        self.hours_var = tk.StringVar(value="")
        self.extended_hours_var = tk.BooleanVar(value=False)
        self.display_mode_var = tk.StringVar(value="Prices")
        self.compare_visible_var = tk.BooleanVar(value=False)
        self.rebase_comparison_var = tk.BooleanVar(value=False)
        self.betas_comparison_var = tk.BooleanVar(value=False)
        self.technical_study: tuple[str, int] | None = None
        self.intraday_start_var = tk.StringVar(value="")
        self.intraday_end_var = tk.StringVar(value="")
        self.intraday_custom_bar_var = tk.StringVar(value="15m")
        self.historical_start_var = tk.StringVar(value="")
        self.historical_end_var = tk.StringVar(value="")
        self.chart_group_var = tk.StringVar(value="A")
        self.watchlist_group_var = tk.StringVar(value="A")
        self.search_action_var = tk.StringVar(value="OPEN SECURITY")
        self.search_sort_var = tk.StringVar(value="Relevance")
        self.exchange_filter_var = tk.StringVar(value="All Markets")
        self.results: list[Instrument] = []
        self.raw_results: list[Instrument] = []
        self.chart_instruments: list[Instrument] = []
        self.watchlist_instruments: dict[str, Instrument] = {}
        self.watchlist_target_item: str | None = None
        self.watchlist_editor: tk.Entry | None = None
        self.add_to_compare_mode = False
        self.suggestion_anchor = None
        self.selected_instrument: Instrument | None = None
        self.selected_range = INTRADAY_RANGES[0]
        self.range_buttons: list[ttk.Button] = []
        self.technical_buttons: list[tuple[ttk.Button, tuple[str, int] | None]] = []
        self.range_popup_mode: str | None = None
        self.range_hide_after_id: str | None = None
        self.search_request_id = 0
        self.search_after_id: str | None = None
        self.chart_request_id = 0
        self.current_frame = pd.DataFrame()
        self.current_frames: dict[str, pd.DataFrame] = {}
        self.current_session = MarketSession()
        self.beta_model_stats: BetaModelStats | None = None
        self.hover_artists = []
        self.measurement_mode = False
        self.measurement_points: list[tuple[pd.Timestamp, float]] = []
        self.measurement_artists = []
        self.text_selection_dragging = False
        self.text_selection_start: tuple[int, int] | None = None
        self.text_selection_borders: list[tk.Toplevel] = []
        self.floating_window_drag: dict[str, int] | None = None
        self.floating_window_resize: dict[str, int] | None = None
        self.layout_save_after_id: str | None = None
        self.layout_manually_saved = False
        self.saved_layout_snapshot: dict = {}
        self.layout_dirty = False
        self.geometry_save_after_id: str | None = None
        source_root = Path(__file__).resolve().parent
        self.source_watch_paths = tuple(source_root / name for name in RUNTIME_SOURCE_FILES)
        self.source_snapshot = source_file_snapshot(self.source_watch_paths)

        self._configure_styles()
        self._build_controls()
        self._build_chart()
        self.search_var.trace_add("write", self._on_search_text_changed)
        self.watchlist_search_var.trace_add("write", self._on_watchlist_search_text_changed)
        self.bind("<ButtonPress-1>", self._dismiss_suggestions_on_click, add="+")
        self.bind_all("<Control-f>", self._focus_primary_search)
        self.bind_all("<Control-F>", self._focus_primary_search)
        self.bind_all("<ButtonPress-1>", self._start_text_rectangle, add="+")
        self.bind_all("<B1-Motion>", self._drag_text_rectangle, add="+")
        self.bind_all("<ButtonRelease-1>", self._finish_text_rectangle, add="+")
        self.bind_all("<Escape>", self._cancel_text_selection, add="+")
        self.bind("<Configure>", self._schedule_window_geometry_save, add="+")
        self.protocol("WM_DELETE_WINDOW", self._close_app)
        self._set_mode("Intraday")
        self.after_idle(self._restore_window_state)
        self.after(SOURCE_WATCH_INTERVAL_MS, self._poll_for_source_update)

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Status.TLabel", background=BG, foreground=MUTED)
        style.configure(
            "Update.TLabel",
            background=ORANGE,
            foreground=BG,
            font=("Segoe UI", 10, "bold"),
        )
        style.configure(
            "Title.TLabel", background=BG, foreground=ORANGE, font=("Consolas", 17, "bold")
        )
        style.configure(
            "Quote.TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 12, "bold")
        )
        style.configure(
            "TButton",
            background=PANEL,
            foreground=TEXT,
            bordercolor=GRID,
            padding=(10, 7),
        )
        style.map("TButton", background=[("active", GRID)])
        style.configure(
            "Accent.TButton",
            background=ORANGE,
            foreground=BG,
            bordercolor=ORANGE,
            font=("Segoe UI", 10, "bold"),
        )
        style.map("Accent.TButton", background=[("active", "#ffc247")])
        style.configure(
            "Chip.TButton",
            background=PANEL,
            foreground=MUTED,
            bordercolor=GRID,
            padding=(9, 5),
            font=("Segoe UI", 9, "bold"),
        )
        style.map("Chip.TButton", background=[("active", GRID)], foreground=[("active", TEXT)])
        style.configure(
            "Selected.Chip.TButton",
            background=ORANGE,
            foreground=BG,
            bordercolor=ORANGE,
            padding=(9, 5),
            font=("Segoe UI", 9, "bold"),
        )
        style.map("Selected.Chip.TButton", background=[("active", "#ffc247")])
        style.configure(
            "Flyout.TButton",
            background=PANEL,
            foreground=TEXT,
            bordercolor=GRID,
            padding=(7, 5),
            font=("Segoe UI", 9),
        )
        style.map("Flyout.TButton", background=[("active", GRID)])
        style.configure(
            "Selected.Flyout.TButton",
            background=ORANGE,
            foreground=BG,
            bordercolor=ORANGE,
            padding=(7, 5),
            font=("Segoe UI", 9, "bold"),
        )
        style.configure(
            "Treeview",
            background=PANEL,
            fieldbackground=PANEL,
            foreground=TEXT,
            rowheight=27,
        )
        style.configure("Treeview.Heading", background=GRID, foreground=TEXT)
        style.map("Treeview", background=[("selected", ORANGE)], foreground=[("selected", BG)])
        style.configure("TCheckbutton", background=BG, foreground=MUTED)
        style.configure(
            "Chip.TCheckbutton",
            background=PANEL,
            foreground=MUTED,
            indicatorcolor=PANEL,
            padding=(8, 5),
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "Chip.TCheckbutton",
            background=[("active", GRID)],
            foreground=[("selected", TEXT), ("active", TEXT)],
            indicatorcolor=[("selected", ORANGE)],
        )

    def _build_controls(self) -> None:
        header = ttk.Frame(self, padding=(18, 13, 18, 8))
        header.pack(fill=tk.X)
        ttk.Label(header, text="MARKET TERMINAL", style="Title.TLabel").pack(side=tk.LEFT)
        ttk.Label(
            header, text="  market chart workspace", style="Status.TLabel"
        ).pack(side=tk.LEFT, pady=(5, 0))
        ttk.Button(
            header,
            text="SAVE LAYOUT",
            style="Chip.TButton",
            command=self._manual_save_function_layout,
        ).pack(side=tk.RIGHT, pady=(2, 0))
        ttk.Button(
            header,
            text="DATA STATUS",
            style="Chip.TButton",
            command=self._show_provider_status,
        ).pack(side=tk.RIGHT, padx=(0, 8), pady=(2, 0))

        self._build_update_banner()
        workspace = ttk.Frame(self, padding=(18, 0, 18, 0))
        workspace.pack(fill=tk.BOTH, expand=True)
        self.desktop = tk.Frame(workspace, bg=BG)
        self.desktop.pack(fill=tk.BOTH, expand=True)
        self.desktop.bind("<Configure>", self._constrain_chart_window_to_desktop)
        self.chart_window = tk.Frame(
            self.desktop,
            bg=PANEL,
            highlightbackground=GRID,
            highlightthickness=1,
        )
        self.chart_window.place(x=0, y=0, width=960, height=560)
        self.chart_titlebar = tk.Frame(self.chart_window, bg=GRID, height=28, cursor="fleur")
        self.chart_titlebar.pack(fill=tk.X)
        self.chart_titlebar.pack_propagate(False)
        title_label = tk.Label(
            self.chart_titlebar,
            text="CHART",
            bg=GRID,
            fg=TEXT,
            font=("Segoe UI", 9, "bold"),
            padx=9,
        )
        title_label.pack(side=tk.LEFT)
        title_label.bind("<ButtonPress-1>", self._start_chart_window_drag)
        title_label.bind("<B1-Motion>", self._drag_chart_window)
        title_label.bind("<ButtonRelease-1>", self._finish_chart_window_drag)
        self._build_group_selector(
            self.chart_titlebar,
            self.chart_group_var,
            self._on_chart_group_changed,
        )
        self.search_entry = tk.Entry(
            self.chart_titlebar,
            textvariable=self.search_var,
            bg=BG,
            fg=TEXT,
            insertbackground=TEXT,
            relief=tk.FLAT,
            font=("Segoe UI", 10),
        )
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4, padx=(6, 8), pady=3)
        self.search_entry.bind("<Return>", self._accept_or_search)
        self.search_entry.bind("<Control-a>", self._select_all_search_text)
        self.search_entry.bind("<Control-A>", self._select_all_search_text)
        self.search_entry.bind("<Control-BackSpace>", self._delete_previous_search_word)
        self.search_entry.bind("<Escape>", lambda _event: self._hide_transient_panels())
        self.search_entry.bind("<Down>", lambda _event: self._move_suggestion_selection(1))
        self.search_entry.bind("<Up>", lambda _event: self._move_suggestion_selection(-1))
        self.search_entry.bind("<FocusIn>", lambda _event: self._set_suggestion_anchor(self.search_entry))
        tk.Button(
            self.chart_titlebar,
            text="MAX",
            command=self._maximize_chart_window,
            bg=GRID,
            fg=MUTED,
            activebackground=PANEL,
            activeforeground=TEXT,
            relief=tk.FLAT,
            font=("Segoe UI", 8, "bold"),
            padx=8,
            pady=2,
        ).pack(side=tk.RIGHT, padx=(0, 3), pady=3)
        self.chart_titlebar.bind("<ButtonPress-1>", self._start_chart_window_drag)
        self.chart_titlebar.bind("<B1-Motion>", self._drag_chart_window)
        self.chart_titlebar.bind("<ButtonRelease-1>", self._finish_chart_window_drag)
        self.chart_panel = ttk.Frame(self.chart_window, style="Panel.TFrame", padding=(8, 7, 8, 0))
        self.chart_panel.pack(fill=tk.BOTH, expand=True)
        self.chart_resize_grip = tk.Frame(
            self.chart_window,
            bg=ORANGE,
            width=15,
            height=15,
            cursor="size_nw_se",
        )
        self.chart_resize_grip.place(relx=1.0, rely=1.0, anchor=tk.SE)
        self.chart_resize_grip.bind("<ButtonPress-1>", self._start_chart_window_resize)
        self.chart_resize_grip.bind("<B1-Motion>", self._resize_chart_window)
        self.chart_resize_grip.bind("<ButtonRelease-1>", self._finish_chart_window_resize)
        ttk.Label(self.chart_panel, textvariable=self.identity_var, style="Status.TLabel").pack(
            anchor=tk.W, pady=(0, 3)
        )
        ttk.Label(self.chart_panel, textvariable=self.quote_var, style="Quote.TLabel").pack(
            anchor=tk.W, pady=(0, 6)
        )
        ttk.Label(self.chart_panel, textvariable=self.fundamentals_var, style="Status.TLabel").pack(
            anchor=tk.W, pady=(0, 4)
        )
        ttk.Label(
            self.chart_panel,
            textvariable=self.sec_context_var,
            style="Status.TLabel",
            wraplength=1200,
        ).pack(anchor=tk.W, pady=(0, 4))
        ttk.Label(self.chart_panel, textvariable=self.measurement_var, style="Status.TLabel").pack(
            anchor=tk.W, pady=(0, 4)
        )
        self._build_suggestion_popup()
        self._build_watchlist_window()
        self.after_idle(self._layout_initial_workspace_windows)
        self.after_idle(self.refresh_watchlist)

    def _layout_initial_workspace_windows(self) -> None:
        self.desktop.update_idletasks()
        if self.desktop.winfo_width() < MIN_CHART_WINDOW_WIDTH or self.desktop.winfo_height() < MIN_CHART_WINDOW_HEIGHT:
            self.after(80, self._layout_initial_workspace_windows)
            return
        if self._restore_saved_function_layout():
            self._mark_layout_saved_snapshot()
            return
        width = max(self.desktop.winfo_width(), MIN_CHART_WINDOW_WIDTH + 360)
        height = max(self.desktop.winfo_height(), MIN_CHART_WINDOW_HEIGHT)
        watch_width = min(430, max(360, int(width * 0.32)))
        chart_width = max(MIN_CHART_WINDOW_WIDTH, width - watch_width - 10)
        self.watchlist_window.place_configure(
            x=0,
            y=0,
            width=watch_width,
            height=min(height, 520),
        )
        self.chart_window.place_configure(x=watch_width + 10, y=0, width=chart_width, height=height)
        self._mark_layout_saved_snapshot()

    def _restore_saved_function_layout(self) -> bool:
        if not self.saved_layout_state:
            return False
        restored = False
        for name, widget, minimum_width, minimum_height in (
            ("watchlist", self.watchlist_window, 360, 260),
            ("chart", self.chart_window, MIN_CHART_WINDOW_WIDTH, MIN_CHART_WINDOW_HEIGHT),
        ):
            geometry = self.saved_layout_state.get(name)
            if not isinstance(geometry, dict):
                continue
            x = int(geometry.get("x", 0))
            y = int(geometry.get("y", 0))
            width = max(int(geometry.get("width", minimum_width)), minimum_width)
            height = max(int(geometry.get("height", minimum_height)), minimum_height)
            widget.place_configure(x=x, y=y, width=width, height=height)
            restored = True
        self._constrain_chart_window_to_desktop(None)
        self._constrain_watchlist_window_to_desktop()
        self.after(250, self._apply_saved_function_layout_without_constraints)
        return restored

    def _apply_saved_function_layout_without_constraints(self) -> None:
        for name, widget in (
            ("watchlist", self.watchlist_window),
            ("chart", self.chart_window),
        ):
            geometry = self.saved_layout_state.get(name)
            if not isinstance(geometry, dict):
                continue
            widget.place_configure(
                x=int(geometry.get("x", widget.winfo_x())),
                y=int(geometry.get("y", widget.winfo_y())),
                width=int(geometry.get("width", widget.winfo_width())),
                height=int(geometry.get("height", widget.winfo_height())),
            )

    def _build_group_selector(
        self, parent: tk.Widget, variable: tk.StringVar, command
    ) -> tk.OptionMenu:
        menu = tk.OptionMenu(parent, variable, *tuple("ABCDEF"), command=command)
        menu.configure(
            bg=ORANGE,
            fg=BG,
            activebackground="#ffc247",
            activeforeground=BG,
            relief=tk.FLAT,
            font=("Segoe UI", 8, "bold"),
            padx=2,
            pady=0,
            width=1,
            highlightthickness=0,
        )
        menu["menu"].configure(bg=PANEL, fg=TEXT, activebackground=ORANGE, activeforeground=BG)
        menu.pack(side=tk.LEFT, padx=(2, 4), pady=3)
        return menu

    def _on_chart_group_changed(self, _value: str | None = None) -> None:
        self.status_var.set(f"Chart linked to group {self.chart_group_var.get()}.")

    def _on_watchlist_group_changed(self, _value: str | None = None) -> None:
        self.status_var.set(f"Watchlist linked to group {self.watchlist_group_var.get()}.")

    def _maximize_chart_window(self) -> None:
        self.desktop.update_idletasks()
        width = max(self.desktop.winfo_width(), MIN_CHART_WINDOW_WIDTH)
        height = max(self.desktop.winfo_height(), MIN_CHART_WINDOW_HEIGHT)
        self.chart_window.place_configure(x=0, y=0, width=width, height=height)
        self._mark_layout_dirty_if_changed()

    def _start_chart_window_drag(self, event: tk.Event) -> str:
        self.chart_window.lift()
        self.floating_window_drag = {
            "x": event.x_root,
            "y": event.y_root,
            "left": self.chart_window.winfo_x(),
            "top": self.chart_window.winfo_y(),
        }
        return "break"

    def _drag_chart_window(self, event: tk.Event) -> str:
        if not self.floating_window_drag:
            return "break"
        desktop_width = max(self.desktop.winfo_width(), MIN_CHART_WINDOW_WIDTH)
        desktop_height = max(self.desktop.winfo_height(), MIN_CHART_WINDOW_HEIGHT)
        width = self.chart_window.winfo_width()
        height = self.chart_window.winfo_height()
        left = self.floating_window_drag["left"] + event.x_root - self.floating_window_drag["x"]
        top = self.floating_window_drag["top"] + event.y_root - self.floating_window_drag["y"]
        left = max(0, min(left, max(desktop_width - width, 0)))
        top = max(0, min(top, max(desktop_height - height, 0)))
        self.chart_window.place_configure(x=left, y=top)
        self._mark_layout_dirty_if_changed()
        return "break"

    def _finish_chart_window_drag(self, _event: tk.Event) -> str:
        self.floating_window_drag = None
        self._mark_layout_dirty_if_changed()
        return "break"

    def _start_chart_window_resize(self, event: tk.Event) -> str:
        self.chart_window.lift()
        self.floating_window_resize = {
            "x": event.x_root,
            "y": event.y_root,
            "width": self.chart_window.winfo_width(),
            "height": self.chart_window.winfo_height(),
        }
        return "break"

    def _resize_chart_window(self, event: tk.Event) -> str:
        if not self.floating_window_resize:
            return "break"
        left = self.chart_window.winfo_x()
        top = self.chart_window.winfo_y()
        desktop_width = max(self.desktop.winfo_width(), MIN_CHART_WINDOW_WIDTH)
        desktop_height = max(self.desktop.winfo_height(), MIN_CHART_WINDOW_HEIGHT)
        width = self.floating_window_resize["width"] + event.x_root - self.floating_window_resize["x"]
        height = self.floating_window_resize["height"] + event.y_root - self.floating_window_resize["y"]
        width = max(MIN_CHART_WINDOW_WIDTH, min(width, max(desktop_width - left, MIN_CHART_WINDOW_WIDTH)))
        height = max(MIN_CHART_WINDOW_HEIGHT, min(height, max(desktop_height - top, MIN_CHART_WINDOW_HEIGHT)))
        self.chart_window.place_configure(width=width, height=height)
        self._mark_layout_dirty_if_changed()
        return "break"

    def _finish_chart_window_resize(self, _event: tk.Event) -> str:
        self.floating_window_resize = None
        self._mark_layout_dirty_if_changed()
        return "break"

    def _constrain_chart_window_to_desktop(self, _event: tk.Event) -> None:
        if not hasattr(self, "chart_window"):
            return
        if self.desktop.winfo_width() < MIN_CHART_WINDOW_WIDTH or self.desktop.winfo_height() < MIN_CHART_WINDOW_HEIGHT:
            return
        desktop_width = max(self.desktop.winfo_width(), MIN_CHART_WINDOW_WIDTH)
        desktop_height = max(self.desktop.winfo_height(), MIN_CHART_WINDOW_HEIGHT)
        left = max(0, min(self.chart_window.winfo_x(), max(desktop_width - MIN_CHART_WINDOW_WIDTH, 0)))
        top = max(0, min(self.chart_window.winfo_y(), max(desktop_height - MIN_CHART_WINDOW_HEIGHT, 0)))
        width = min(max(self.chart_window.winfo_width(), MIN_CHART_WINDOW_WIDTH), desktop_width - left)
        height = min(max(self.chart_window.winfo_height(), MIN_CHART_WINDOW_HEIGHT), desktop_height - top)
        self.chart_window.place_configure(x=left, y=top, width=width, height=height)
        if hasattr(self, "watchlist_window"):
            self._constrain_watchlist_window_to_desktop()

    def _constrain_watchlist_window_to_desktop(self) -> None:
        if self.desktop.winfo_width() < 360 or self.desktop.winfo_height() < 260:
            return
        desktop_width = max(self.desktop.winfo_width(), 360)
        desktop_height = max(self.desktop.winfo_height(), 260)
        left = max(0, min(self.watchlist_window.winfo_x(), max(desktop_width - 360, 0)))
        top = max(0, min(self.watchlist_window.winfo_y(), max(desktop_height - 260, 0)))
        width = min(max(self.watchlist_window.winfo_width(), 360), desktop_width - left)
        height = min(max(self.watchlist_window.winfo_height(), 260), desktop_height - top)
        self.watchlist_window.place_configure(x=left, y=top, width=width, height=height)

    def _start_watchlist_window_drag(self, event: tk.Event) -> str:
        self.watchlist_window.lift()
        self.floating_window_drag = {
            "x": event.x_root,
            "y": event.y_root,
            "left": self.watchlist_window.winfo_x(),
            "top": self.watchlist_window.winfo_y(),
        }
        return "break"

    def _drag_watchlist_window(self, event: tk.Event) -> str:
        if not self.floating_window_drag:
            return "break"
        desktop_width = max(self.desktop.winfo_width(), 360)
        desktop_height = max(self.desktop.winfo_height(), 300)
        width = self.watchlist_window.winfo_width()
        height = self.watchlist_window.winfo_height()
        left = self.floating_window_drag["left"] + event.x_root - self.floating_window_drag["x"]
        top = self.floating_window_drag["top"] + event.y_root - self.floating_window_drag["y"]
        left = max(0, min(left, max(desktop_width - width, 0)))
        top = max(0, min(top, max(desktop_height - height, 0)))
        self.watchlist_window.place_configure(x=left, y=top)
        self._mark_layout_dirty_if_changed()
        return "break"

    def _finish_watchlist_window_drag(self, _event: tk.Event) -> str:
        self.floating_window_drag = None
        self._mark_layout_dirty_if_changed()
        return "break"

    def _start_watchlist_window_resize(self, event: tk.Event) -> str:
        self.watchlist_window.lift()
        self.floating_window_resize = {
            "x": event.x_root,
            "y": event.y_root,
            "width": self.watchlist_window.winfo_width(),
            "height": self.watchlist_window.winfo_height(),
        }
        return "break"

    def _resize_watchlist_window(self, event: tk.Event) -> str:
        if not self.floating_window_resize:
            return "break"
        left = self.watchlist_window.winfo_x()
        top = self.watchlist_window.winfo_y()
        desktop_width = max(self.desktop.winfo_width(), 360)
        desktop_height = max(self.desktop.winfo_height(), 300)
        width = self.floating_window_resize["width"] + event.x_root - self.floating_window_resize["x"]
        height = self.floating_window_resize["height"] + event.y_root - self.floating_window_resize["y"]
        width = max(360, min(width, max(desktop_width - left, 360)))
        height = max(260, min(height, max(desktop_height - top, 260)))
        self.watchlist_window.place_configure(width=width, height=height)
        self._mark_layout_dirty_if_changed()
        return "break"

    def _finish_watchlist_window_resize(self, _event: tk.Event) -> str:
        self.floating_window_resize = None
        self._mark_layout_dirty_if_changed()
        return "break"

    def _build_watchlist_window(self) -> None:
        self.watchlist_window = tk.Frame(
            self.desktop,
            bg=PANEL,
            highlightbackground=GRID,
            highlightthickness=1,
        )
        self.watchlist_window.place(x=980, y=0, width=400, height=500)
        self.watchlist_titlebar = tk.Frame(self.watchlist_window, bg=GRID, height=28, cursor="fleur")
        self.watchlist_titlebar.pack(fill=tk.X)
        self.watchlist_titlebar.pack_propagate(False)
        label = tk.Label(
            self.watchlist_titlebar,
            text="WATCHLIST",
            bg=GRID,
            fg=TEXT,
            font=("Segoe UI", 9, "bold"),
            padx=9,
        )
        label.pack(side=tk.LEFT)
        for widget in (self.watchlist_titlebar, label):
            widget.bind("<ButtonPress-1>", self._start_watchlist_window_drag)
            widget.bind("<B1-Motion>", self._drag_watchlist_window)
            widget.bind("<ButtonRelease-1>", self._finish_watchlist_window_drag)
        self._build_group_selector(
            self.watchlist_titlebar,
            self.watchlist_group_var,
            self._on_watchlist_group_changed,
        )
        tk.Button(
            self.watchlist_titlebar,
            text="REFRESH",
            command=self.refresh_watchlist,
            bg=GRID,
            fg=MUTED,
            activebackground=PANEL,
            activeforeground=TEXT,
            relief=tk.FLAT,
            font=("Segoe UI", 8, "bold"),
            padx=8,
            pady=2,
        ).pack(side=tk.RIGHT, padx=(0, 3), pady=3)
        content = ttk.Frame(self.watchlist_window, style="Panel.TFrame", padding=7)
        content.pack(fill=tk.BOTH, expand=True)
        self.watchlist_tree = ttk.Treeview(
            content,
            columns=("asset", "last", "bid", "ask", "volume"),
            show="headings",
            height=12,
        )
        for column, title, width in (
            ("asset", "Asset", 145),
            ("last", "Last", 70),
            ("bid", "Bid", 70),
            ("ask", "Ask", 70),
            ("volume", "Volume", 90),
        ):
            self.watchlist_tree.heading(column, text=title)
            self.watchlist_tree.column(column, width=width, anchor=tk.W)
        self.watchlist_tree.pack(fill=tk.BOTH, expand=True)
        self.watchlist_tree.bind("<Double-Button-1>", self._begin_watchlist_asset_search)
        self.watchlist_tree.bind("<<TreeviewSelect>>", self._on_watchlist_selection_changed)
        actions = ttk.Frame(content, style="Panel.TFrame")
        actions.pack(fill=tk.X, pady=(7, 0))
        ttk.Button(actions, text="ADD ROW", command=self._add_watchlist_row).pack(side=tk.LEFT)
        ttk.Button(actions, text="REMOVE", command=self._remove_watchlist_row).pack(
            side=tk.LEFT, padx=(5, 0)
        )
        ttk.Label(
            content,
            text="Double-click Asset to search and fill row.",
            style="Status.TLabel",
        ).pack(anchor=tk.W, pady=(6, 0))
        self.watchlist_resize_grip = tk.Frame(
            self.watchlist_window,
            bg=ORANGE,
            width=15,
            height=15,
            cursor="size_nw_se",
        )
        self.watchlist_resize_grip.place(relx=1.0, rely=1.0, anchor=tk.SE)
        self.watchlist_resize_grip.bind("<ButtonPress-1>", self._start_watchlist_window_resize)
        self.watchlist_resize_grip.bind("<B1-Motion>", self._resize_watchlist_window)
        self.watchlist_resize_grip.bind("<ButtonRelease-1>", self._finish_watchlist_window_resize)
        for row in self.saved_watchlist_state:
            self._add_watchlist_row(row)
        for _position in range(max(8 - len(self.saved_watchlist_state), 0)):
            self._add_watchlist_row()

    def _add_watchlist_row(self, row: dict | None = None) -> None:
        item = f"wl{len(self.watchlist_tree.get_children()) + 1}"
        instrument = instrument_from_watchlist_row(row or {})
        values = (watchlist_asset_label(instrument), "", "", "", "") if instrument else ("", "", "", "", "")
        self.watchlist_tree.insert("", tk.END, iid=item, values=values)
        if instrument:
            self.watchlist_instruments[item] = instrument

    def _remove_watchlist_row(self) -> None:
        for item in self.watchlist_tree.selection():
            self.watchlist_instruments.pop(item, None)
            self.watchlist_tree.delete(item)
        self._save_watchlist_state()

    def _on_watchlist_selection_changed(self, _event: tk.Event | None = None) -> None:
        selected = self.watchlist_tree.selection()
        if not selected:
            return
        instrument = self.watchlist_instruments.get(selected[0])
        if instrument is None:
            return
        self._open_grouped_chart_from_watchlist(instrument)

    def _open_grouped_chart_from_watchlist(self, instrument: Instrument) -> None:
        if self.watchlist_group_var.get() != self.chart_group_var.get():
            return
        if self.chart_instruments and self.chart_instruments[0].symbol == instrument.symbol:
            return
        self.status_var.set(
            f"Group {self.watchlist_group_var.get()}: opening {instrument.symbol} in chart."
        )
        self._open_instrument(instrument)

    def _begin_watchlist_asset_search(self, event: tk.Event) -> str:
        item = self.watchlist_tree.identify_row(event.y)
        column = self.watchlist_tree.identify_column(event.x)
        if not item or column != "#1":
            return "break"
        self._destroy_watchlist_editor()
        self.watchlist_target_item = item
        self.add_to_compare_mode = False
        self.search_action_var.set("SET WATCHLIST ASSET")
        bounds = self.watchlist_tree.bbox(item, column)
        if not bounds:
            return "break"
        x, y, width, height = bounds
        current = self.watchlist_tree.set(item, "asset")
        self.watchlist_search_var.set(current)
        self.watchlist_editor = tk.Entry(
            self.watchlist_tree,
            textvariable=self.watchlist_search_var,
            bg=BG,
            fg=TEXT,
            insertbackground=TEXT,
            relief=tk.FLAT,
            font=("Segoe UI", 10),
        )
        self.watchlist_editor.place(x=x, y=y, width=width, height=height)
        self.watchlist_editor.focus_set()
        self.watchlist_editor.selection_range(0, tk.END)
        self.watchlist_editor.bind("<Return>", self._accept_or_search)
        self.watchlist_editor.bind("<Escape>", lambda _event: self._cancel_watchlist_editor())
        self.watchlist_editor.bind("<Down>", lambda _event: self._move_suggestion_selection(1))
        self.watchlist_editor.bind("<Up>", lambda _event: self._move_suggestion_selection(-1))
        self.watchlist_editor.bind(
            "<FocusIn>", lambda _event: self._set_suggestion_anchor(self.watchlist_editor)
        )
        self.suggestion_anchor = self.watchlist_editor
        if self.watchlist_search_var.get().strip():
            self.search_assets()
        self.status_var.set("Type in the watchlist cell, then select a matching asset.")
        return "break"

    def _cancel_watchlist_editor(self) -> str:
        self.watchlist_target_item = None
        self._destroy_watchlist_editor()
        self._hide_suggestions(restore_focus=False)
        self.search_action_var.set("OPEN SECURITY")
        self.watchlist_search_var.set("")
        return "break"

    def _destroy_watchlist_editor(self) -> None:
        if self.watchlist_editor is not None:
            self.watchlist_editor.destroy()
            self.watchlist_editor = None

    def _build_update_banner(self) -> None:
        self.update_banner = tk.Frame(
            self,
            bg=ORANGE,
            padx=12,
            pady=7,
        )
        ttk.Label(
            self.update_banner,
            text="NEW VERSION AVAILABLE  |  App source has changed. Reload to apply updates.",
            style="Update.TLabel",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Button(
            self.update_banner,
            text="RELOAD APP",
            command=self._reload_app,
            bg=BG,
            fg=TEXT,
            activebackground=GRID,
            activeforeground=TEXT,
            relief=tk.FLAT,
            font=("Segoe UI", 9, "bold"),
            padx=10,
            pady=4,
        ).pack(side=tk.RIGHT, padx=(8, 0))
        tk.Button(
            self.update_banner,
            text="LATER",
            command=self._dismiss_update_banner,
            bg=ORANGE,
            fg=BG,
            activebackground="#ffc247",
            activeforeground=BG,
            relief=tk.FLAT,
            font=("Segoe UI", 9),
            padx=8,
            pady=4,
        ).pack(side=tk.RIGHT, padx=(8, 0))

    def _poll_for_source_update(self) -> None:
        current_snapshot = source_file_snapshot(self.source_watch_paths)
        if current_snapshot != self.source_snapshot and not self.update_banner.winfo_ismapped():
            self.update_banner.pack(
                fill=tk.X,
                padx=18,
                pady=(0, 10),
            )
        self.after(SOURCE_WATCH_INTERVAL_MS, self._poll_for_source_update)

    def _dismiss_update_banner(self) -> None:
        self.source_snapshot = source_file_snapshot(self.source_watch_paths)
        self.update_banner.pack_forget()

    def _reload_app(self) -> None:
        self._save_window_state()
        os.execl(sys.executable, sys.executable, *sys.argv)

    def _restore_window_state(self) -> None:
        if self.saved_window_state["state"] == "zoomed":
            self.state("zoomed")

    def _schedule_window_geometry_save(self, event: tk.Event) -> None:
        if event.widget is not self or self.state() != "normal":
            return
        if self.geometry_save_after_id:
            self.after_cancel(self.geometry_save_after_id)
        self.geometry_save_after_id = self.after(300, self._save_window_geometry)

    def _save_window_geometry(self) -> None:
        self.geometry_save_after_id = None
        save_window_geometry(self.window_state_path, self.geometry(), "normal")

    def _save_window_state(self) -> None:
        state = self.state()
        if state == "normal":
            save_window_geometry(self.window_state_path, self.geometry(), "normal")
        elif state == "zoomed":
            save_window_geometry(
                self.window_state_path,
                load_window_geometry(self.window_state_path) or DEFAULT_WINDOW_GEOMETRY,
                "zoomed",
            )

    def _close_app(self) -> None:
        self._save_window_state()
        self._save_watchlist_state()
        if self.layout_save_after_id:
            self.after_cancel(self.layout_save_after_id)
        if self.layout_dirty:
            save_layout = messagebox.askyesno(
                "Unsaved layout changes",
                "You have unsaved changes in your layout. Save them?",
                parent=self,
            )
            if save_layout:
                self._save_function_layout()
        self.destroy()

    def _schedule_function_layout_save(self) -> None:
        if self.layout_save_after_id:
            self.after_cancel(self.layout_save_after_id)
        self.layout_save_after_id = self.after(120, self._save_function_layout)

    def _manual_save_function_layout(self) -> None:
        self._save_function_layout(show_status=True)
        self.layout_manually_saved = True

    def _show_provider_status(self) -> None:
        summary = provider_health_summary()
        self.status_var.set("Provider status report opened.")
        messagebox.showinfo("Data Provider Status", summary, parent=self)

    def _save_function_layout(self, show_status: bool = False) -> None:
        self.layout_save_after_id = None
        self.update_idletasks()
        layout = {
            "watchlist": window_place_geometry(self.watchlist_window),
            "chart": window_place_geometry(self.chart_window),
        }
        save_layout_state(self.layout_state_path, layout)
        self.saved_layout_state = layout
        self.saved_layout_snapshot = layout
        self.layout_dirty = False
        if show_status:
            watch = layout["watchlist"]
            chart = layout["chart"]
            self.status_var.set(
                "Layout saved"
                f" | Watchlist {watch['width']}x{watch['height']}+{watch['x']}+{watch['y']}"
                f" | Chart {chart['width']}x{chart['height']}+{chart['x']}+{chart['y']}"
            )

    def _current_function_layout(self) -> dict:
        self.update_idletasks()
        return {
            "watchlist": window_place_geometry(self.watchlist_window),
            "chart": window_place_geometry(self.chart_window),
        }

    def _mark_layout_saved_snapshot(self) -> None:
        self.saved_layout_snapshot = self._current_function_layout()
        self.layout_dirty = False

    def _mark_layout_dirty_if_changed(self) -> None:
        self.layout_dirty = self._current_function_layout() != self.saved_layout_snapshot

    def _build_suggestion_popup(self) -> None:
        self.suggestion_popup = tk.Toplevel(self)
        self.suggestion_popup.withdraw()
        self.suggestion_popup.overrideredirect(True)
        self.suggestion_popup.configure(bg=ORANGE)
        suggestion_panel = ttk.Frame(self.suggestion_popup, style="Panel.TFrame", padding=7)
        suggestion_panel.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        ttk.Label(suggestion_panel, textvariable=self.search_action_var, style="Status.TLabel").pack(
            anchor=tk.W, pady=(0, 5)
        )
        filters = ttk.Frame(suggestion_panel, style="Panel.TFrame")
        filters.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(filters, text="Sort", style="Status.TLabel").pack(side=tk.LEFT)
        self.search_sort = ttk.Combobox(
            filters,
            textvariable=self.search_sort_var,
            values=("Relevance", "Market Cap", "Exchange"),
            width=12,
            state="readonly",
        )
        self.search_sort.pack(side=tk.LEFT, padx=(5, 12))
        self.search_sort.bind("<<ComboboxSelected>>", lambda _event: self._render_search_results())
        ttk.Label(filters, text="Market", style="Status.TLabel").pack(side=tk.LEFT)
        self.exchange_filter = ttk.Combobox(
            filters,
            textvariable=self.exchange_filter_var,
            values=("All Markets",),
            width=18,
            state="readonly",
        )
        self.exchange_filter.pack(side=tk.LEFT, padx=(5, 0))
        self.exchange_filter.bind(
            "<<ComboboxSelected>>", lambda _event: self._render_search_results()
        )
        self.result_tree = ttk.Treeview(
            suggestion_panel,
            columns=("symbol", "name", "venue", "cap"),
            show="headings",
            height=8,
        )
        self.result_tree.heading("symbol", text="Symbol")
        self.result_tree.heading("name", text="Description")
        self.result_tree.heading("venue", text="Market")
        self.result_tree.heading("cap", text="Mkt Cap")
        self.result_tree.column("symbol", width=120)
        self.result_tree.column("name", width=285)
        self.result_tree.column("venue", width=130)
        self.result_tree.column("cap", width=100)
        self.result_tree.pack(fill=tk.BOTH, expand=True)
        self.result_tree.bind("<Double-Button-1>", lambda _event: self._accept_search_result())
        self.result_tree.bind("<Return>", lambda _event: self._accept_search_result())
        self.result_tree.bind("<Escape>", lambda _event: self._hide_suggestions())
        self.result_tree.bind("<Down>", lambda _event: self._move_suggestion_selection(1))
        self.result_tree.bind("<Up>", lambda _event: self._move_suggestion_selection(-1))
        actions = ttk.Frame(suggestion_panel, style="Panel.TFrame")
        actions.pack(fill=tk.X, pady=(7, 0))
        ttk.Button(actions, text="OPEN", command=self._open_search_result).pack(
            side=tk.LEFT, padx=(0, 5)
        )
        ttk.Button(
            actions, text="ADD TO COMPARE", style="Accent.TButton", command=self._add_search_result
        ).pack(
            side=tk.LEFT
        )
        ttk.Label(
            actions, text="  Enter / double-click: highlighted action", style="Status.TLabel"
        ).pack(side=tk.LEFT, padx=(7, 0))

    def _build_compare_panel(self) -> None:
        self.compare_panel = tk.Frame(
            self.chart_panel,
            bg=PANEL,
            highlightbackground=ORANGE,
            highlightthickness=1,
            width=310,
        )
        panel_content = ttk.Frame(self.compare_panel, style="Panel.TFrame", padding=8)
        panel_content.pack(fill=tk.BOTH, expand=True)
        ttk.Label(panel_content, text="COMPARISON SERIES (MAX 10)", style="Status.TLabel").pack(
            anchor=tk.W, pady=(0, 5)
        )
        mode_controls = ttk.Frame(panel_content, style="Panel.TFrame")
        mode_controls.pack(fill=tk.X, pady=(0, 6))
        self.rebase_check = ttk.Checkbutton(
            mode_controls,
            text="REB 100",
            style="Chip.TCheckbutton",
            variable=self.rebase_comparison_var,
            command=self._set_comparison_rebase,
        )
        self.rebase_check.pack(side=tk.LEFT)
        self.betas_check = ttk.Checkbutton(
            mode_controls,
            text="BETAS",
            style="Chip.TCheckbutton",
            variable=self.betas_comparison_var,
            command=self._set_comparison_betas,
        )
        self.betas_check.pack(side=tk.LEFT, padx=(5, 0))
        self.beta_summary_var = tk.StringVar(value="")
        self.beta_summary_label = ttk.Label(
            panel_content, textvariable=self.beta_summary_var, style="Status.TLabel"
        )
        self.series_tree = ttk.Treeview(
            panel_content, columns=("symbol", "name", "venue"), show="headings", height=8
        )
        self.series_tree.heading("symbol", text="Symbol")
        self.series_tree.heading("name", text="Description")
        self.series_tree.heading("venue", text="Market")
        self.series_tree.column("symbol", width=72)
        self.series_tree.column("name", width=128)
        self.series_tree.column("venue", width=75)
        self.series_tree.pack(fill=tk.BOTH, expand=True)
        self.series_tree.bind("<Escape>", lambda _event: self._hide_compare_panel())
        self.compare_search_entry = tk.Entry(
            panel_content,
            textvariable=self.search_var,
            bg=BG,
            fg=TEXT,
            insertbackground=TEXT,
            relief=tk.FLAT,
            font=("Segoe UI", 10),
        )
        self.compare_search_entry.pack(fill=tk.X, pady=(7, 3), ipady=5)
        self.compare_search_entry.bind("<Return>", self._accept_or_search)
        self.compare_search_entry.bind("<Down>", lambda _event: self._move_suggestion_selection(1))
        self.compare_search_entry.bind("<Up>", lambda _event: self._move_suggestion_selection(-1))
        self.compare_search_entry.bind("<Escape>", lambda _event: self._hide_transient_panels())
        self.compare_search_entry.bind(
            "<FocusIn>", lambda _event: self._set_suggestion_anchor(self.compare_search_entry)
        )
        ttk.Label(
            panel_content,
            text="Add series: type above, then Arrow + Enter",
            style="Status.TLabel",
        ).pack(anchor=tk.W, pady=(0, 3))
        actions = ttk.Frame(panel_content, style="Panel.TFrame")
        actions.pack(fill=tk.X, pady=(7, 0))
        ttk.Button(actions, text="REMOVE", command=self._remove_chart_series).pack(
            side=tk.LEFT, padx=(0, 5)
        )
        ttk.Button(actions, text="CLEAR", command=self._clear_chart_series).pack(side=tk.LEFT)

    def _select_all_search_text(self, _event: tk.Event | None = None) -> str:
        self.search_entry.selection_range(0, tk.END)
        self.search_entry.icursor(tk.END)
        return "break"

    def _focus_primary_search(self, _event: tk.Event | None = None) -> str:
        self._hide_compare_panel()
        def apply_focus() -> None:
            self._set_suggestion_anchor(self.search_entry)
            self.search_entry.focus_set()
            self.search_entry.selection_range(0, tk.END)
            self.search_entry.icursor(tk.END)

        apply_focus()
        self.after_idle(apply_focus)
        return "break"

    def _delete_previous_search_word(self, _event: tk.Event | None = None) -> str:
        if self.search_entry.selection_present():
            self.search_entry.delete(tk.SEL_FIRST, tk.SEL_LAST)
            return "break"
        cursor = self.search_entry.index(tk.INSERT)
        if cursor == 0:
            return "break"
        before_cursor = self.search_entry.get()[:cursor]
        boundary = len(before_cursor.rstrip())
        while boundary > 0 and not before_cursor[boundary - 1].isspace():
            boundary -= 1
        self.search_entry.delete(boundary, cursor)
        return "break"

    def _on_search_text_changed(self, *_args) -> None:
        if self.search_after_id:
            self.after_cancel(self.search_after_id)
            self.search_after_id = None
        query = self.search_var.get().strip()
        if not query:
            self.search_request_id += 1
            self.results = []
            self.raw_results = []
            self._hide_suggestions(restore_focus=False)
            return
        self.search_after_id = self.after(SEARCH_DEBOUNCE_MS, self.search_assets)

    def _on_watchlist_search_text_changed(self, *_args) -> None:
        if self.suggestion_anchor != self.watchlist_editor:
            return
        if self.search_after_id:
            self.after_cancel(self.search_after_id)
            self.search_after_id = None
        query = self.watchlist_search_var.get().strip()
        if not query:
            self.search_request_id += 1
            self.results = []
            self.raw_results = []
            self._hide_suggestions(restore_focus=False)
            return
        self.search_after_id = self.after(SEARCH_DEBOUNCE_MS, self.search_assets)

    def _active_search_query(self) -> str:
        if self.suggestion_anchor == self.watchlist_editor:
            return self.watchlist_search_var.get().strip()
        return self.search_var.get().strip()

    def _set_suggestion_anchor(self, entry) -> None:
        self.suggestion_anchor = entry
        if entry == self.compare_search_entry:
            self.add_to_compare_mode = True
            self.search_action_var.set("ADD SECURITY TO COMPARISON")
        elif entry == self.watchlist_editor:
            self.add_to_compare_mode = False
            self.search_action_var.set("SET WATCHLIST ASSET")
        else:
            self.add_to_compare_mode = False
            self.search_action_var.set("OPEN SECURITY")

    def _show_suggestions(self) -> None:
        self.update_idletasks()
        anchor = self.suggestion_anchor or self.search_entry
        height = 338
        is_compare = anchor == self.compare_search_entry
        is_watchlist = anchor == self.watchlist_editor
        minimum_width = 420 if is_watchlist else 625 if is_compare else 720
        preferred_width = (
            max(minimum_width, anchor.winfo_width() + 320)
            if is_watchlist
            else minimum_width if is_compare else max(minimum_width, anchor.winfo_width())
        )
        window_left = self.winfo_rootx() + 12
        window_right = self.winfo_rootx() + self.winfo_width() - 12
        window_top = self.winfo_rooty() + 12
        window_bottom = self.winfo_rooty() + self.winfo_height() - 12
        x, y, width = fit_popup_to_window(
            anchor_x=anchor.winfo_rootx(),
            anchor_y=anchor.winfo_rooty(),
            anchor_width=anchor.winfo_width(),
            anchor_height=anchor.winfo_height(),
            preferred_width=preferred_width,
            popup_height=height,
            window_left=window_left,
            window_right=window_right,
            window_top=window_top,
            window_bottom=window_bottom,
            align_right=is_compare,
        )
        self._size_suggestion_columns(width, is_compare or is_watchlist)
        self.suggestion_popup.geometry(f"{width}x{height}+{x}+{y}")
        self.suggestion_popup.deiconify()
        self.suggestion_popup.lift()

    def _size_suggestion_columns(self, width: int, is_compare: bool) -> None:
        available = max(width - 20, 300)
        symbol_width = 82 if is_compare else 110
        venue_width = 105 if is_compare else 125
        cap_width = 92 if is_compare else 105
        name_width = max(available - symbol_width - venue_width - cap_width, 135)
        self.result_tree.column("symbol", width=symbol_width)
        self.result_tree.column("name", width=name_width)
        self.result_tree.column("venue", width=venue_width)
        self.result_tree.column("cap", width=cap_width)

    def _hide_suggestions(self, restore_focus: bool = True) -> str:
        self.suggestion_popup.withdraw()
        if restore_focus:
            anchor = self.suggestion_anchor or self.search_entry
            if anchor.winfo_exists():
                anchor.focus_set()
        return "break"

    def _move_suggestion_selection(self, direction: int) -> str:
        if self.suggestion_popup.state() == "withdrawn" or not self.results:
            return "break"
        items = self.result_tree.get_children()
        selected = self.result_tree.selection()
        if not selected:
            position = 0 if direction > 0 else len(items) - 1
        else:
            current = items.index(selected[0])
            position = max(0, min(len(items) - 1, current + direction))
        item = items[position]
        self.result_tree.selection_set(item)
        self.result_tree.focus(item)
        self.result_tree.see(item)
        return "break"

    def _dismiss_suggestions_on_click(self, event: tk.Event) -> None:
        if not self.text_selection_dragging:
            self._clear_text_selection_outline()
        active_anchors = [self.search_entry]
        if self.watchlist_editor is not None:
            active_anchors.append(self.watchlist_editor)
        if event.widget not in active_anchors and self.suggestion_popup.state() != "withdrawn":
            self.suggestion_popup.withdraw()
        if event.widget not in (
            self.time_range_button,
            self.technical_button,
        ):
            self._hide_range_popup()

    def _toggle_compare_panel(self) -> None:
        if self.compare_visible_var.get():
            self.compare_panel.pack(
                side=tk.RIGHT,
                fill=tk.Y,
                padx=(8, 0),
                before=self.chart_canvas_widget,
            )
            self._begin_add_to_compare()
            return
        self._keep_primary_series_only()
        self._hide_compare_panel()

    def _keep_primary_series_only(self) -> None:
        if len(self.chart_instruments) <= 1:
            return
        primary = self.chart_instruments[0]
        self.chart_instruments = [primary]
        self.selected_instrument = primary
        self.current_frames = {
            primary.symbol: self.current_frames[primary.symbol]
        } if primary.symbol in self.current_frames else {}
        self.current_frame = self.current_frames.get(primary.symbol, pd.DataFrame())
        self.beta_model_stats = None
        self.rebase_comparison_var.set(False)
        self.betas_comparison_var.set(False)
        self.display_mode_var.set("Prices")
        self._update_series_tree()
        self.status_var.set("Comparison series removed; primary asset remains open.")

    def _hide_compare_panel(self) -> str:
        self.compare_visible_var.set(False)
        self.compare_panel.pack_forget()
        self.rebase_comparison_var.set(False)
        self.betas_comparison_var.set(False)
        self.beta_summary_var.set("")
        self.beta_summary_label.pack_forget()
        self.display_mode_var.set("Prices")
        self._configure_series_tree_columns()
        self._redraw_current_chart()
        if self.suggestion_anchor == self.compare_search_entry:
            self._hide_suggestions(restore_focus=False)
            self._set_suggestion_anchor(self.search_entry)
        return "break"

    def _hide_transient_panels(self) -> str:
        self.suggestion_popup.withdraw()
        self._hide_compare_panel()
        self._hide_range_popup()
        return "break"

    def _begin_add_to_compare(self) -> None:
        self.add_to_compare_mode = True
        self.search_action_var.set("ADD SECURITY TO COMPARISON")
        self.suggestion_anchor = self.compare_search_entry
        self.compare_search_entry.focus_set()
        self.compare_search_entry.selection_range(0, tk.END)
        if self.search_var.get().strip() and self.results:
            self.result_tree.selection_remove(*self.result_tree.get_children())
            self.result_tree.focus("")
            self._show_suggestions()
        self.status_var.set("Type a security and select it to add to the comparison.")

    def _build_chart(self) -> None:
        self.figure = Figure(figsize=(8, 5), dpi=100, facecolor=BG)
        grid = self.figure.add_gridspec(4, 1, hspace=0.02)
        self.price_axis = self.figure.add_subplot(grid[:3, 0])
        self.volume_axis = self.figure.add_subplot(grid[3, 0], sharex=self.price_axis)
        self.study_axis = self.volume_axis.twinx()
        self.canvas = FigureCanvasTkAgg(self.figure, master=self.chart_panel)
        self.chart_canvas_widget = self.canvas.get_tk_widget()
        self.chart_canvas_widget.pack(fill=tk.BOTH, expand=True)
        self._build_chart_toolbar()
        self.chart_menu = tk.Menu(
            self,
            tearoff=False,
            bg=PANEL,
            fg=TEXT,
            activebackground=ORANGE,
            activeforeground=BG,
        )
        self.chart_menu.add_command(
            label="% / Points Return (select two dots)",
            command=self._start_return_measurement,
        )
        self.chart_menu.add_command(label="Clear Measurement", command=self._clear_measurement)
        self.chart_menu.add_separator()
        self.chart_menu.add_command(label="Reset Zoom", command=self._reset_zoom)
        self.canvas.mpl_connect("button_press_event", self._on_chart_button_press)
        self.canvas.mpl_connect("motion_notify_event", self._on_chart_hover)
        self.canvas.mpl_connect("axes_leave_event", self._hide_hover)
        self._clear_chart("Enter a security name or identifier above.")
        ttk.Label(
            self.chart_panel, textvariable=self.status_var, style="Status.TLabel", padding=(0, 7)
        ).pack(
            fill=tk.X
        )
        ttk.Label(
            self.chart_panel, textvariable=self.session_var, style="Status.TLabel", padding=(0, 0)
        ).pack(fill=tk.X)
        ttk.Label(
            self.chart_panel, textvariable=self.hours_var, style="Status.TLabel", padding=(0, 5)
        ).pack(fill=tk.X)

    def _build_chart_toolbar(self) -> None:
        self.chart_toolbar = tk.Frame(
            self.chart_canvas_widget,
            bg=PANEL,
            highlightbackground=GRID,
            highlightthickness=1,
            padx=5,
            pady=5,
        )
        self.chart_toolbar.place(x=54, y=14)
        self.time_range_button = ttk.Button(
            self.chart_toolbar,
            text="TIME RANGE",
            style="Selected.Chip.TButton",
        )
        self.time_range_button.pack(side=tk.LEFT)
        self.time_range_button.bind(
            "<Enter>", lambda _event: self._show_range_popup("Time Range", self.time_range_button)
        )
        self.time_range_button.bind("<Leave>", lambda _event: self._schedule_range_popup_hide())
        self.technical_button = ttk.Button(
            self.chart_toolbar,
            text="TECHNICAL",
            style="Chip.TButton",
        )
        self.technical_button.pack(side=tk.LEFT, padx=(4, 10))
        self.technical_button.bind(
            "<Enter>", lambda _event: self._show_range_popup("Technical", self.technical_button)
        )
        self.technical_button.bind("<Leave>", lambda _event: self._schedule_range_popup_hide())
        self.extended_hours_check = ttk.Checkbutton(
            self.chart_toolbar,
            text="EXT HRS",
            variable=self.extended_hours_var,
            command=self.refresh_chart,
        )
        self.extended_hours_check.pack(side=tk.LEFT, padx=(0, 8))
        self.extended_hours_check.state(["disabled"])
        self.compare_button = ttk.Checkbutton(
            self.chart_toolbar,
            text="COMPARE [0]",
            style="Chip.TCheckbutton",
            variable=self.compare_visible_var,
            command=self._toggle_compare_panel,
        )
        self.compare_button.pack(side=tk.LEFT)
        self.chart_toolbar.lift()
        self._build_range_popup()
        self._build_compare_panel()

    def _start_text_rectangle(self, event: tk.Event) -> str | None:
        if not self._text_at_screen_point(event.x_root, event.y_root):
            return None
        self._clear_text_selection_outline()
        self.text_selection_dragging = True
        self.text_selection_start = (event.x_root, event.y_root)
        self._show_text_selection_outline(
            normalized_rectangle(self.text_selection_start, self.text_selection_start)
        )
        if hasattr(self, "zoom_selector"):
            self.zoom_selector.set_active(False)
        self.status_var.set("Drag to select displayed text; release to copy it.")
        return "break"

    def _drag_text_rectangle(self, event: tk.Event) -> str | None:
        if not self.text_selection_dragging or not self.text_selection_start:
            return None
        self._show_text_selection_outline(
            normalized_rectangle(self.text_selection_start, (event.x_root, event.y_root))
        )
        return "break"

    def _finish_text_rectangle(self, event: tk.Event) -> str | None:
        if not self.text_selection_dragging or not self.text_selection_start:
            return None
        bounds = normalized_rectangle(self.text_selection_start, (event.x_root, event.y_root))
        copied_text = ""
        if rectangle_is_drag(bounds):
            self._show_text_selection_outline(bounds)
            copied_text = self._selected_screen_text(bounds)
        else:
            self._clear_text_selection_outline()
        self.text_selection_dragging = False
        self.text_selection_start = None
        if hasattr(self, "zoom_selector"):
            self.zoom_selector.set_active(not self.measurement_mode)
        if copied_text:
            self.clipboard_clear()
            self.clipboard_append(copied_text)
            self.update_idletasks()
            self.status_var.set(
                f"Copied selected text to clipboard ({len(copied_text.splitlines())} line(s))."
            )
        elif rectangle_is_drag(bounds):
            self.status_var.set("No selectable displayed text found inside the rectangle.")
        return "break"

    def _cancel_text_selection(self, _event: tk.Event | None = None) -> str | None:
        if not self.text_selection_dragging and not self.text_selection_borders:
            return None
        self.text_selection_dragging = False
        self.text_selection_start = None
        self._clear_text_selection_outline()
        if hasattr(self, "zoom_selector"):
            self.zoom_selector.set_active(not self.measurement_mode)
        self.status_var.set("Text selection cleared.")
        return "break"

    def _show_text_selection_outline(self, bounds: tuple[int, int, int, int]) -> None:
        x1, y1, x2, y2 = bounds
        width = max(x2 - x1, 2)
        height = max(y2 - y1, 2)
        pieces = (
            (x1, y1, width, 2),
            (x1, y2 - 2, width, 2),
            (x1, y1, 2, height),
            (x2 - 2, y1, 2, height),
        )
        if not self.text_selection_borders:
            for _piece in pieces:
                border = tk.Toplevel(self)
                border.overrideredirect(True)
                border.configure(bg=ORANGE)
                border.attributes("-topmost", True)
                self.text_selection_borders.append(border)
        for border, (left, top, part_width, part_height) in zip(
            self.text_selection_borders, pieces
        ):
            border.geometry(f"{max(part_width, 2)}x{max(part_height, 2)}+{left}+{top}")
            border.deiconify()
            border.lift()

    def _clear_text_selection_outline(self) -> None:
        for border in self.text_selection_borders:
            border.destroy()
        self.text_selection_borders = []

    def _selected_screen_text(self, bounds: tuple[int, int, int, int]) -> str:
        blocks: list[tuple[int, int, str]] = []
        for widget in walk_visible_widgets(self):
            if isinstance(widget, ttk.Treeview):
                blocks.extend(tree_text_blocks(widget, bounds))
                continue
            text = displayed_widget_text(widget)
            if text and rectangles_intersect(widget_screen_bounds(widget), bounds):
                blocks.append((widget.winfo_rooty(), widget.winfo_rootx(), text))
        blocks.extend(self._selected_chart_text(bounds))
        return ordered_text_blocks(blocks)

    def _text_at_screen_point(self, x: int, y: int) -> bool:
        point = (x, y, x, y)
        for widget in walk_visible_widgets(self):
            if isinstance(
                widget,
                (tk.Entry, tk.Button, ttk.Button, ttk.Checkbutton, ttk.Combobox),
            ):
                continue
            if isinstance(widget, ttk.Treeview):
                if tree_text_at_point(widget, x, y):
                    return True
                continue
            if rectangles_intersect(displayed_text_bounds(widget), point):
                return True
        return bool(self._selected_chart_text(point))

    def _selected_chart_text(
        self, bounds: tuple[int, int, int, int]
    ) -> list[tuple[int, int, str]]:
        blocks: list[tuple[int, int, str]] = []
        renderer = self.canvas.get_renderer()
        canvas_left = self.chart_canvas_widget.winfo_rootx()
        canvas_top = self.chart_canvas_widget.winfo_rooty()
        canvas_height = self.chart_canvas_widget.winfo_height()
        artists = []
        for axis in self.figure.axes:
            artists.extend(axis.texts)
            artists.extend(axis.get_xticklabels())
            artists.extend(axis.get_yticklabels())
            artists.extend((axis.xaxis.label, axis.yaxis.label))
            legend = axis.get_legend()
            if legend:
                artists.extend(legend.get_texts())
        for artist in artists:
            if not artist.get_visible():
                continue
            text = artist.get_text().strip()
            if not text:
                continue
            artist_bounds = artist.get_window_extent(renderer)
            left = canvas_left + int(artist_bounds.x0)
            right = canvas_left + int(artist_bounds.x1)
            top = canvas_top + canvas_height - int(artist_bounds.y1)
            bottom = canvas_top + canvas_height - int(artist_bounds.y0)
            if rectangles_intersect((left, top, right, bottom), bounds):
                blocks.append((top, left, text))
        return blocks

    def _build_range_popup(self) -> None:
        self.range_popup = tk.Toplevel(self)
        self.range_popup.withdraw()
        self.range_popup.overrideredirect(True)
        self.range_popup.configure(bg=ORANGE)
        self.range_popup.bind("<Enter>", lambda _event: self._cancel_range_popup_hide())
        self.range_popup.bind("<Leave>", lambda _event: self._schedule_range_popup_hide())

    def _show_range_popup(self, mode: str, anchor) -> None:
        self._cancel_range_popup_hide()
        if self.range_popup_mode != mode:
            for widget in self.range_popup.winfo_children():
                widget.destroy()
            panel = ttk.Frame(self.range_popup, style="Panel.TFrame", padding=8)
            panel.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
            panel.bind("<Enter>", lambda _event: self._cancel_range_popup_hide())
            panel.bind("<Leave>", lambda _event: self._schedule_range_popup_hide())
            self.range_buttons = []
            self.technical_buttons = []
            if mode == "Time Range":
                self._build_time_range_flyout(panel)
            else:
                self._build_technical_flyout(panel)
            self.range_popup_mode = mode
        self._update_range_selection()
        self.update_idletasks()
        x = anchor.winfo_rootx()
        y = anchor.winfo_rooty() + anchor.winfo_height() + 3
        self.range_popup.geometry(f"+{x}+{y}")
        self.range_popup.deiconify()
        self.range_popup.lift()

    def _build_time_range_flyout(self, panel) -> None:
        ttk.Label(panel, text="TIME RANGE | INTRADAY WINDOW / BAR", style="Status.TLabel").grid(
            row=0, column=0, columnspan=7, sticky=tk.W, pady=(0, 5)
        )
        bar_columns = ("1m", "5m", "15m", "30m", "60m")
        for column, interval in enumerate(bar_columns, start=1):
            ttk.Label(panel, text=interval, style="Status.TLabel").grid(
                row=1, column=column, padx=3, pady=(0, 3)
            )
        for row, (duration, specs) in enumerate(INTRADAY_MATRIX, start=2):
            ttk.Label(panel, text=duration, style="Status.TLabel").grid(
                row=row, column=0, padx=(0, 7), pady=2
            )
            for range_spec in specs:
                column = bar_columns.index(range_spec.interval) + 1
                button = ttk.Button(
                    panel,
                    text=range_spec.interval,
                    width=4,
                    style="Flyout.TButton",
                    command=lambda value=range_spec: self._choose_range(value, "Intraday"),
                )
                button.grid(row=row, column=column, padx=2, pady=2)
                button.bind("<Enter>", lambda _event: self._cancel_range_popup_hide())
                self.range_buttons.append((button, range_spec))
        custom_intraday_row = len(INTRADAY_MATRIX) + 2
        intraday_custom = ttk.Frame(panel, style="Panel.TFrame")
        intraday_custom.grid(
            row=custom_intraday_row,
            column=0,
            columnspan=7,
            sticky=tk.W,
            pady=(7, 1),
        )
        ttk.Label(intraday_custom, text="CUSTOM YYYY-MM-DD", style="Status.TLabel").pack(
            side=tk.LEFT
        )
        self._build_date_entry(intraday_custom, self.intraday_start_var).pack(
            side=tk.LEFT, padx=(7, 4)
        )
        ttk.Label(intraday_custom, text="to", style="Status.TLabel").pack(side=tk.LEFT)
        self._build_date_entry(intraday_custom, self.intraday_end_var).pack(
            side=tk.LEFT, padx=(4, 6)
        )
        interval = ttk.Combobox(
            intraday_custom,
            textvariable=self.intraday_custom_bar_var,
            values=("1m", "5m", "15m", "30m", "60m"),
            width=5,
            state="readonly",
        )
        interval.pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            intraday_custom,
            text="APPLY",
            style="Flyout.TButton",
            command=lambda: self._apply_custom_range("Intraday"),
        ).pack(side=tk.LEFT)
        historical_row = custom_intraday_row + 1
        ttk.Separator(panel, orient=tk.HORIZONTAL).grid(
            row=historical_row, column=0, columnspan=7, sticky=tk.EW, pady=(7, 5)
        )
        ttk.Label(panel, text="HISTORICAL", style="Status.TLabel").grid(
            row=historical_row + 1, column=0, sticky=tk.W, padx=(0, 7), pady=2
        )
        for column, range_spec in enumerate(HISTORICAL_RANGES, start=1):
            button = ttk.Button(
                panel,
                text=range_spec.label,
                width=5,
                style="Flyout.TButton",
                command=lambda value=range_spec: self._choose_range(value, "Historical"),
            )
            button.grid(row=historical_row + 1, column=column, padx=2, pady=2)
            button.bind("<Enter>", lambda _event: self._cancel_range_popup_hide())
            self.range_buttons.append((button, range_spec))
        historical_custom = ttk.Frame(panel, style="Panel.TFrame")
        historical_custom.grid(
            row=historical_row + 2,
            column=0,
            columnspan=7,
            sticky=tk.W,
            pady=(7, 1),
        )
        ttk.Label(historical_custom, text="CUSTOM YYYY-MM-DD", style="Status.TLabel").pack(
            side=tk.LEFT
        )
        self._build_date_entry(historical_custom, self.historical_start_var).pack(
            side=tk.LEFT, padx=(7, 4)
        )
        ttk.Label(historical_custom, text="to", style="Status.TLabel").pack(side=tk.LEFT)
        self._build_date_entry(historical_custom, self.historical_end_var).pack(
            side=tk.LEFT, padx=(4, 6)
        )
        ttk.Button(
            historical_custom,
            text="APPLY DAILY",
            style="Flyout.TButton",
            command=lambda: self._apply_custom_range("Historical"),
        ).pack(side=tk.LEFT)

    def _build_date_entry(self, parent, variable: tk.StringVar) -> tk.Entry:
        return tk.Entry(
            parent,
            textvariable=variable,
            width=11,
            bg=BG,
            fg=TEXT,
            insertbackground=TEXT,
            relief=tk.FLAT,
            font=("Segoe UI", 9),
        )

    def _build_technical_flyout(self, panel) -> None:
        ttk.Label(panel, text="TECHNICAL ANALYSIS", style="Status.TLabel").grid(
            row=0, column=0, columnspan=4, sticky=tk.W, pady=(0, 6)
        )
        studies = (
            ("DISPLAY", (("VOLUME", None),)),
            ("RSI", (("RSI 7", ("RSI", 7)), ("RSI 14", ("RSI", 14)), ("RSI 21", ("RSI", 21)))),
            (
                "MOM %",
                (
                    ("MOM 5", ("MOM", 5)),
                    ("MOM 10", ("MOM", 10)),
                    ("MOM 20", ("MOM", 20)),
                ),
            ),
            (
                "SIGMA %",
                (
                    ("SIG 10", ("SIGMA", 10)),
                    ("SIG 20", ("SIGMA", 20)),
                    ("SIG 60", ("SIGMA", 60)),
                ),
            ),
        )
        self.technical_buttons = []
        for row, (label, options) in enumerate(studies, start=1):
            ttk.Label(panel, text=label, style="Status.TLabel").grid(
                row=row, column=0, sticky=tk.W, padx=(0, 8), pady=2
            )
            for column, (text, study) in enumerate(options, start=1):
                button = ttk.Button(
                    panel,
                    text=text,
                    width=8,
                    style="Flyout.TButton",
                    command=lambda value=study: self._choose_technical_study(value),
                )
                button.grid(row=row, column=column, padx=2, pady=2)
                button.bind("<Enter>", lambda _event: self._cancel_range_popup_hide())
                self.technical_buttons.append((button, study))

    def _cancel_range_popup_hide(self) -> None:
        if self.range_hide_after_id:
            self.after_cancel(self.range_hide_after_id)
            self.range_hide_after_id = None

    def _schedule_range_popup_hide(self) -> None:
        self._cancel_range_popup_hide()
        self.range_hide_after_id = self.after(180, self._hide_range_popup)

    def _hide_range_popup(self) -> None:
        self.range_hide_after_id = None
        self.range_popup.withdraw()

    def _set_mode(self, mode: str) -> None:
        self.mode_var.set(mode)
        if mode == "Intraday":
            self.selected_range = INTRADAY_RANGES[0]
        else:
            self.selected_range = HISTORICAL_RANGES[0]
        self._update_range_selection()
        if self.chart_instruments:
            self.refresh_chart()

    def _choose_range(self, range_spec: RangeSpec, mode: str | None = None) -> None:
        if mode:
            self.mode_var.set(mode)
        self.selected_range = range_spec
        self._update_range_selection()
        self._hide_range_popup()
        self.refresh_chart()

    def _apply_custom_range(self, mode: str) -> None:
        try:
            if mode == "Intraday":
                range_spec = custom_range_spec(
                    mode,
                    self.intraday_start_var.get(),
                    self.intraday_end_var.get(),
                    self.intraday_custom_bar_var.get(),
                )
            else:
                range_spec = custom_range_spec(
                    mode,
                    self.historical_start_var.get(),
                    self.historical_end_var.get(),
                    "1d",
                )
        except ValueError as exc:
            self.status_var.set(str(exc))
            return
        self._choose_range(range_spec, mode)

    def _update_range_selection(self) -> None:
        self.time_range_button.configure(
            style="Selected.Chip.TButton",
            text=f"TIME RANGE  {self.selected_range.label}",
        )
        for button, range_spec in self.range_buttons:
            button.configure(
                style="Selected.Flyout.TButton"
                if range_spec == self.selected_range and self.range_popup_mode == "Time Range"
                else "Flyout.TButton"
            )
        self.technical_button.configure(
            style="Selected.Chip.TButton" if self.technical_study else "Chip.TButton",
            text=f"TECHNICAL  {technical_study_label(self.technical_study)}"
            if self.technical_study
            else "TECHNICAL",
        )
        for button, study in self.technical_buttons:
            button.configure(
                style="Selected.Flyout.TButton"
                if study == self.technical_study and self.range_popup_mode == "Technical"
                else "Flyout.TButton"
            )

    def _choose_technical_study(self, study: tuple[str, int] | None) -> None:
        self.technical_study = study
        self._update_range_selection()
        self._hide_range_popup()
        self._redraw_current_chart()

    def _set_comparison_rebase(self) -> None:
        self.display_mode_var.set(
            "Rebased 100" if self.rebase_comparison_var.get() else "Prices"
        )
        self._redraw_current_chart()

    def _set_comparison_betas(self) -> None:
        self._update_beta_model()
        self._configure_series_tree_columns()
        self._update_series_tree()

    def _configure_series_tree_columns(self) -> None:
        if self.betas_comparison_var.get():
            self.series_tree.configure(columns=("symbol", "beta", "stderr", "tstat", "pvalue"))
            for column, title, width in (
                ("symbol", "Series", 68),
                ("beta", "Beta", 54),
                ("stderr", "SE", 52),
                ("tstat", "t", 48),
                ("pvalue", "p", 52),
            ):
                self.series_tree.heading(column, text=title)
                self.series_tree.column(column, width=width)
            self.beta_summary_label.pack(fill=tk.X, pady=(0, 5), before=self.series_tree)
            return
        self.series_tree.configure(columns=("symbol", "name", "venue"))
        for column, title, width in (
            ("symbol", "Symbol", 72),
            ("name", "Description", 128),
            ("venue", "Market", 75),
        ):
            self.series_tree.heading(column, text=title)
            self.series_tree.column(column, width=width)
        self.beta_summary_label.pack_forget()

    def search_assets(self) -> None:
        if self.search_after_id:
            self.after_cancel(self.search_after_id)
            self.search_after_id = None
        query = self._active_search_query()
        if not query:
            self.search_request_id += 1
            self.results = []
            self.raw_results = []
            self._hide_suggestions(restore_focus=False)
            return
        self.search_request_id += 1
        request_id = self.search_request_id
        self.status_var.set(f"Searching for {query}...")
        self._run_background(
            lambda: self.provider.search(query),
            lambda results: self._show_results(results)
            if request_id == self.search_request_id
            else None,
            "Search failed",
            lambda: request_id == self.search_request_id,
        )

    def _accept_or_search(self, _event: tk.Event | None = None) -> str:
        if self.suggestion_popup.state() != "withdrawn":
            if self.result_tree.selection():
                self._accept_search_result()
            else:
                self.status_var.set("Use Up/Down to highlight a security, then press Enter.")
            return "break"
        self.search_assets()
        return "break"

    def _show_results(self, instruments: list[Instrument]) -> None:
        self.raw_results = instruments
        exchanges = sorted({instrument.exchange for instrument in instruments if instrument.exchange})
        values = ("All Markets", *exchanges)
        self.exchange_filter.configure(values=values)
        if self.exchange_filter_var.get() not in values:
            self.exchange_filter_var.set("All Markets")
        self._render_search_results()

    def _render_search_results(self) -> None:
        self.results = filter_and_sort_instruments(
            self.raw_results,
            self.search_sort_var.get(),
            self.exchange_filter_var.get(),
        )
        self.result_tree.delete(*self.result_tree.get_children())
        for position, instrument in enumerate(self.results):
            self.result_tree.insert(
                "",
                tk.END,
                iid=str(position),
                values=(
                    instrument.symbol,
                    instrument.name,
                    instrument.exchange,
                    format_market_cap(instrument.market_cap),
                ),
            )
        if not self.raw_results:
            self._hide_suggestions(restore_focus=False)
            self.status_var.set("No instruments found. Try a ticker or a more specific name.")
            return
        if not self.results:
            self.status_var.set("No instruments match the selected market filter.")
            self._show_suggestions()
            return
        self.status_var.set(
            f"Showing {len(self.results)} of {len(self.raw_results)} matching instrument(s)"
            f" | Sort: {self.search_sort_var.get()}."
        )
        self.result_tree.selection_remove(*self.result_tree.get_children())
        self.result_tree.focus("")
        self._show_suggestions()

    def _selected_search_instrument(self) -> Instrument | None:
        selected = self.result_tree.selection()
        if not selected:
            return None
        return self.results[int(selected[0])]

    def _accept_search_result(self) -> str:
        if self.watchlist_target_item:
            self._set_watchlist_search_result()
        elif self.add_to_compare_mode:
            self._add_search_result()
        else:
            self._open_search_result()
        return "break"

    def _set_watchlist_search_result(self) -> None:
        instrument = self._selected_search_instrument()
        item = self.watchlist_target_item
        if not instrument or not item:
            return
        self._hide_suggestions(restore_focus=False)
        self.watchlist_target_item = None
        self._destroy_watchlist_editor()
        self.watchlist_instruments[item] = instrument
        self.watchlist_tree.item(
            item,
            values=(watchlist_asset_label(instrument), "Loading", "", "", ""),
        )
        self.search_action_var.set("OPEN SECURITY")
        self.watchlist_search_var.set("")
        self._save_watchlist_state()
        self._refresh_watchlist_item(item, instrument)

    def refresh_watchlist(self) -> None:
        for item, instrument in list(self.watchlist_instruments.items()):
            self._refresh_watchlist_item(item, instrument)

    def _save_watchlist_state(self) -> None:
        rows = []
        for item in self.watchlist_tree.get_children():
            instrument = self.watchlist_instruments.get(item)
            rows.append(watchlist_row_from_instrument(instrument))
        save_watchlist_state(self.watchlist_state_path, rows)

    def _refresh_watchlist_item(self, item: str, instrument: Instrument) -> None:
        self._run_background(
            lambda: self.provider.quote_snapshot(instrument),
            lambda quote: self._update_watchlist_quote(item, instrument, quote),
            "Watchlist quote failed",
            lambda: item in self.watchlist_instruments
            and self.watchlist_instruments[item].symbol == instrument.symbol,
        )

    def _update_watchlist_quote(self, item: str, instrument: Instrument, quote) -> None:
        self.watchlist_tree.item(
            item,
            values=(
                watchlist_asset_label(instrument),
                format_quote_value(quote.last),
                format_quote_value(quote.bid),
                format_quote_value(quote.ask),
                format_volume_value(quote.volume) if quote.volume is not None else "",
            ),
        )

    def _open_search_result(self) -> None:
        instrument = self._selected_search_instrument()
        if instrument:
            self._hide_suggestions()
            self.add_to_compare_mode = False
            self.search_action_var.set("OPEN SECURITY")
            self._open_instrument(instrument)

    def _add_search_result(self) -> None:
        instrument = self._selected_search_instrument()
        if not instrument:
            return
        if any(existing.symbol == instrument.symbol for existing in self.chart_instruments):
            self.status_var.set(f"{instrument.symbol} is already on the chart.")
            return
        if len(self.chart_instruments) >= MAX_SERIES:
            self.status_var.set(f"The chart supports up to {MAX_SERIES} series.")
            return
        self._hide_suggestions()
        self.chart_instruments.append(instrument)
        if len(self.chart_instruments) >= 2 and not self.betas_comparison_var.get():
            self.rebase_comparison_var.set(True)
            self.display_mode_var.set("Rebased 100")
        self._update_series_tree()
        if self.compare_visible_var.get():
            self.add_to_compare_mode = True
            self.search_action_var.set("ADD SECURITY TO COMPARISON")
            self.compare_search_entry.focus_set()
            self.compare_search_entry.selection_range(0, tk.END)
        else:
            self.add_to_compare_mode = False
            self.search_action_var.set("OPEN SECURITY")
        self.refresh_chart()

    def _open_instrument(self, instrument: Instrument) -> None:
        self.chart_instruments = [instrument]
        self.selected_instrument = instrument
        self.rebase_comparison_var.set(False)
        self.betas_comparison_var.set(False)
        self.display_mode_var.set("Prices")
        self._update_series_tree()
        self.refresh_chart()

    def _update_series_tree(self) -> None:
        self.series_tree.delete(*self.series_tree.get_children())
        if self.betas_comparison_var.get():
            self._populate_beta_series_tree()
            self.compare_button.configure(text=f"COMPARE [{len(self.chart_instruments)}]")
            return
        for position, instrument in enumerate(self.chart_instruments):
            self.series_tree.insert(
                "",
                tk.END,
                iid=str(position),
                values=(instrument.symbol, instrument.name, instrument.exchange),
            )
        self.compare_button.configure(text=f"COMPARE [{len(self.chart_instruments)}]")

    def _populate_beta_series_tree(self) -> None:
        if not self.chart_instruments:
            self.beta_summary_var.set("Open a primary series, then add X series.")
            return
        primary = self.chart_instruments[0]
        stats = self.beta_model_stats
        self.series_tree.insert(
            "", tk.END, iid="0", values=(f"Y: {primary.symbol}", "", "", "", "")
        )
        if not stats:
            self.beta_summary_var.set("Add X series and load enough aligned returns.")
            for position, instrument in enumerate(self.chart_instruments[1:], start=1):
                self.series_tree.insert(
                    "", tk.END, iid=str(position), values=(instrument.symbol, "-", "-", "-", "-")
                )
            return
        alpha = stats.alpha
        self.beta_summary_var.set(
            f"Y={stats.y_symbol} | N={stats.observations} | R2={stats.r_squared:.3f} "
            f"| Adj={stats.adjusted_r_squared:.3f}\n"
            f"A={alpha.estimate * 10_000:+.2f}bp SE={alpha.std_error * 10_000:.2f} "
            f"| t={alpha.t_stat:+.2f} "
            f"| p={format_probability(alpha.p_value)}"
        )
        for position, instrument in enumerate(self.chart_instruments[1:], start=1):
            coefficient = stats.betas.get(instrument.symbol)
            values = (
                instrument.symbol,
                f"{coefficient.estimate:+.3f}" if coefficient else "-",
                f"{coefficient.std_error:.3f}" if coefficient else "-",
                f"{coefficient.t_stat:+.2f}" if coefficient else "-",
                format_probability(coefficient.p_value) if coefficient else "-",
            )
            self.series_tree.insert("", tk.END, iid=str(position), values=values)

    def _update_beta_model(self) -> None:
        if len(self.chart_instruments) < 2 or not self.current_frames:
            self.beta_model_stats = None
            return
        symbols = [
            instrument.symbol
            for instrument in self.chart_instruments
            if instrument.symbol in self.current_frames
        ]
        self.beta_model_stats = calculate_beta_model(self.current_frames, symbols)

    def _remove_chart_series(self) -> None:
        selected = self.series_tree.selection()
        if not selected:
            return
        del self.chart_instruments[int(selected[0])]
        if not self.chart_instruments:
            self._clear_chart_series()
            return
        self.selected_instrument = self.chart_instruments[0]
        self._update_series_tree()
        self._hide_compare_panel()
        self.refresh_chart()

    def _clear_chart_series(self) -> None:
        self.chart_instruments = []
        self.selected_instrument = None
        self._update_series_tree()
        self._hide_compare_panel()
        self._clear_chart("Search for an asset, then choose Open or Add.")
        self.identity_var.set("")
        self.quote_var.set("Search for an asset to begin.")
        self.fundamentals_var.set("")
        self.sec_context_var.set("")
        self.session_var.set("")
        self.hours_var.set("")
        self.status_var.set("Chart series cleared.")

    def refresh_chart(self) -> None:
        if not self.chart_instruments:
            return
        self.beta_model_stats = None
        if self.betas_comparison_var.get():
            self._update_series_tree()
        instruments = list(self.chart_instruments)
        range_spec = self.selected_range
        include_extended_hours = self.extended_hours_var.get()
        self.chart_request_id += 1
        request_id = self.chart_request_id
        self.status_var.set(
            f"Loading {len(instruments)} series | {self.mode_var.get()} {range_spec.label}..."
        )
        self._run_background(
            lambda: self._load_chart_frames(instruments, range_spec, include_extended_hours),
            lambda result: self._draw_chart(result[0], range_spec, result[1], result[2])
            if request_id == self.chart_request_id
            else None,
            "Price request failed",
            lambda: request_id == self.chart_request_id,
        )

    def _load_chart_frames(
        self,
        instruments: list[Instrument],
        range_spec: RangeSpec,
        include_extended_hours: bool,
    ) -> tuple[list[Instrument], dict[str, pd.DataFrame], MarketSession]:
        instruments = list(instruments)
        if hasattr(self.provider, "instrument_details"):
            try:
                instruments[0] = self.provider.instrument_details(instruments[0])
            except Exception:
                pass
        frames = {
            instrument.symbol: self.provider.history(
                instrument, range_spec, include_extended_hours
            )
            for instrument in instruments
        }
        try:
            session = (
                self.provider.market_session(instruments[0])
                if hasattr(self.provider, "market_session")
                else MarketSession()
            )
        except Exception:
            session = MarketSession()
        return instruments, frames, session

    def _draw_chart(
        self,
        instruments: list[Instrument],
        range_spec: RangeSpec,
        frames: dict[str, pd.DataFrame],
        session: MarketSession | None = None,
    ) -> None:
        visible_frames = prepare_comparison_frames(frames, range_spec)
        instruments = [
            instrument for instrument in instruments if instrument.symbol in visible_frames
        ]
        if not visible_frames or not instruments:
            self._clear_chart("No price bars returned for this market and range.")
            self.status_var.set("No chart data available for the selected series.")
            return
        primary = instruments[0]
        if self.chart_instruments and self.chart_instruments[0].symbol == primary.symbol:
            self.chart_instruments[0] = primary
        self.selected_instrument = primary
        self.current_frames = visible_frames
        self.current_frame = visible_frames[primary.symbol]
        if session is not None:
            self.current_session = session
        self._update_beta_model()
        self._update_series_tree()
        self._display_market_session(primary, self.current_session)
        self._clear_hover()
        self._clear_measurement()
        self.price_axis.clear()
        self.volume_axis.clear()
        self.study_axis.clear()
        self._style_axes()
        self._install_zoom_selector()
        plot_frames = displayed_close_series(visible_frames, self.display_mode_var.get())
        for position, instrument in enumerate(instruments):
            closes = plot_frames[instrument.symbol]
            self.price_axis.plot(
                closes.index,
                closes,
                color=SERIES_COLORS[position % len(SERIES_COLORS)],
                linewidth=1.5,
                label=instrument.symbol,
            )
        frame = visible_frames[primary.symbol]
        primary_closes = frame["Close"]
        first = float(primary_closes.iloc[0])
        last = float(primary_closes.iloc[-1])
        change = last - first
        pct = (change / first * 100) if first else 0.0
        currency = f" {primary.currency}" if primary.currency else ""
        view_label = "Rebased 100" if self.display_mode_var.get() == "Rebased 100" else "Price"
        self.identity_var.set(instrument_identity_text(primary))
        self.quote_var.set(
            f"{primary.symbol}  {primary.name}    {last:,.4f}{currency}   "
            f"{change:+,.4f} ({pct:+.2f}%)   [{range_spec.label}]"
            f" | {len(instruments)} series | {view_label}"
        )
        self.fundamentals_var.set(instrument_fundamentals_text(primary))
        self._refresh_sec_context(primary)
        ylabel = "Indexed (100)" if self.display_mode_var.get() == "Rebased 100" else "Price"
        self.price_axis.set_ylabel(ylabel, color=MUTED)
        self._draw_lower_panel(frame, primary.symbol)
        self.price_axis.legend(
            loc="upper right",
            facecolor=PANEL,
            edgecolor=GRID,
            labelcolor=TEXT,
            ncol=min(5, len(instruments)),
            fontsize=9,
        )
        self._configure_dates(range_spec)
        self._apply_full_date_bounds()
        self._apply_full_price_bounds()
        self.canvas.draw_idle()
        sources = sorted(
            {
                _display_data_source(frame)
                for frame in visible_frames.values()
            }
        )
        self.status_var.set(
            f"Data: {', '.join(sources)} | {len(instruments)} series | {len(frame):,} primary bars"
            f" | Lower: Volume"
            + (f" + {technical_study_label(self.technical_study)}" if self.technical_study else "")
            + f" | Updated {datetime.now():%Y-%m-%d %H:%M:%S}"
        )

    def _refresh_sec_context(self, instrument: Instrument) -> None:
        symbol = instrument.symbol.strip().upper()
        self.sec_context_var.set("")
        if not symbol or symbol == "FORT_PNL" or any(marker in symbol for marker in ".=^/"):
            return
        request_id = self.chart_request_id
        self.sec_context_var.set("SEC: loading filings and company facts...")

        def load_context():
            try:
                return self.sec_client.company_context(symbol)
            except Exception:
                return None

        self._run_background(
            load_context,
            lambda context: self._update_sec_context(request_id, context),
            "SEC request failed",
            lambda: request_id == self.chart_request_id,
        )

    def _update_sec_context(self, request_id: int, context) -> None:
        if request_id != self.chart_request_id:
            return
        self.sec_context_var.set(format_sec_company_context(context) if context else "")

    def _draw_lower_panel(self, frame: pd.DataFrame, symbol: str) -> None:
        if {"BuyCashEUR", "SellCashEUR"}.issubset(frame.columns):
            buys = frame["BuyCashEUR"].fillna(0)
            sells = frame["SellCashEUR"].fillna(0)
            width = _bar_width(frame)
            self.volume_axis.axhline(0, color=GRID, linewidth=0.8)
            self.volume_axis.bar(
                frame.index,
                buys,
                color=UP,
                alpha=0.58 if self.technical_study else 0.72,
                width=width,
                label="Buys",
            )
            self.volume_axis.bar(
                frame.index,
                -sells,
                color=DOWN,
                alpha=0.58 if self.technical_study else 0.72,
                width=width,
                label="Sells",
            )
            if buys.any() or sells.any():
                self.volume_axis.legend(
                    loc="upper left",
                    facecolor=PANEL,
                    edgecolor=GRID,
                    labelcolor=TEXT,
                    fontsize=8,
                )
                self._label_trade_cash_bars(buys, sells)
            self.volume_axis.set_ylabel(f"Trades {symbol}", color=MUTED)
            self.volume_axis.yaxis.set_major_formatter(_euro_cash_formatter())
        elif "Volume" in frame.columns:
            self.volume_axis.bar(
                frame.index,
                frame["Volume"].fillna(0),
                color=SERIES_COLORS[0],
                alpha=0.38 if self.technical_study else 0.5,
                width=_bar_width(frame),
            )
            self.volume_axis.set_ylabel(f"Vol {symbol}", color=MUTED)
            self.volume_axis.yaxis.set_major_formatter(_volume_formatter())
        self.study_axis.set_visible(bool(self.technical_study))
        if not self.technical_study:
            return
        indicator = technical_indicator(frame["Close"], self.technical_study)
        self.study_axis.plot(
            indicator.index,
            indicator,
            color=ORANGE,
            linewidth=1.15,
        )
        name, _period = self.technical_study
        self.study_axis.set_ylabel(technical_study_label(self.technical_study), color=ORANGE)
        self.study_axis.yaxis.set_major_formatter(_technical_formatter())
        if name == "RSI":
            self.study_axis.set_ylim(0, 100)
            for threshold in (30, 70):
                self.study_axis.axhline(
                    threshold, color=MUTED, linewidth=0.7, linestyle="--", alpha=0.8
                )
        else:
            self.study_axis.axhline(0, color=MUTED, linewidth=0.7, linestyle="--", alpha=0.8)
        if indicator.dropna().empty:
            self.study_axis.text(
                0.5,
                0.5,
                "Not enough bars for selected study",
                transform=self.study_axis.transAxes,
                color=MUTED,
                ha="center",
                va="center",
                fontsize=9,
            )

    def _label_trade_cash_bars(self, buys: pd.Series, sells: pd.Series) -> None:
        maximum = max(float(buys.max() or 0), float(sells.max() or 0))
        if maximum <= 0:
            return
        offset = maximum * 0.035
        for timestamp, value in buys[buys > 0].items():
            self.volume_axis.text(
                timestamp,
                float(value) + offset,
                format_euro_cash_value(float(value), compact=True),
                color=TEXT,
                fontsize=7,
                ha="center",
                va="bottom",
                rotation=90,
            )
        for timestamp, value in sells[sells > 0].items():
            self.volume_axis.text(
                timestamp,
                -float(value) - offset,
                format_euro_cash_value(float(value), compact=True),
                color=TEXT,
                fontsize=7,
                ha="center",
                va="top",
                rotation=90,
            )

    def _redraw_current_chart(self) -> None:
        if self.chart_instruments and self.current_frames:
            self._draw_chart(
                self.chart_instruments,
                self.selected_range,
                self.current_frames,
                self.current_session,
            )

    def _display_market_session(self, primary: Instrument, session: MarketSession) -> None:
        self.session_var.set(
            f"{primary.exchange or primary.symbol} | {session.status} | "
            f"Extended: {session.extended_session} | {session.overnight_session}"
        )
        if session.regular_exchange_hours:
            self.hours_var.set(
                f"Regular exchange hours: {session.regular_exchange_hours} | "
                f"Your local time: {session.regular_local_hours}"
            )
        else:
            self.hours_var.set("Trading session hours unavailable from the public data feed.")
        if session.extended_session == "Pre/Post available":
            self.extended_hours_check.state(["!disabled"])
        else:
            self.extended_hours_var.set(False)
            self.extended_hours_check.state(["disabled"])

    def _on_chart_button_press(self, event) -> None:
        if event.button == MouseButton.RIGHT and event.inaxes in (
            self.price_axis,
            self.volume_axis,
            self.study_axis,
        ):
            try:
                self.chart_menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())
            finally:
                self.chart_menu.grab_release()
            return
        if (
            event.button == MouseButton.LEFT
            and self.measurement_mode
            and event.inaxes == self.price_axis
            and event.xdata is not None
            and not self.current_frame.empty
        ):
            self._capture_measurement_point(event.xdata)

    def _on_chart_hover(self, event) -> None:
        if (
            event.inaxes == self.volume_axis
            and event.xdata is not None
            and not self.current_frame.empty
            and not self.measurement_mode
        ):
            self._draw_volume_hover(event.xdata)
            return
        if (
            event.inaxes != self.price_axis
            or event.xdata is None
            or not self.current_frames
            or self.measurement_mode
        ):
            self._hide_hover()
            return
        displayed = displayed_close_series(self.current_frames, self.display_mode_var.get())
        timestamp, values = nearest_displayed_values(displayed, event.xdata)
        self._draw_hover(timestamp, values)

    def _draw_hover(self, timestamp: pd.Timestamp, values: list[tuple[str, float]]) -> None:
        self._clear_hover()
        guide = self.price_axis.axvline(timestamp, color=MUTED, linewidth=0.8, linestyle="--")
        self.hover_artists.append(guide)
        for position, (_symbol, value) in enumerate(values):
            marker = self.price_axis.scatter(
                [timestamp],
                [value],
                color=SERIES_COLORS[position % len(SERIES_COLORS)],
                s=34,
                edgecolors=BG,
                zorder=7,
            )
            self.hover_artists.append(marker)
        label = self._format_chart_time(timestamp)
        suffix = " (Base 100)" if self.display_mode_var.get() == "Rebased 100" else ""
        lines = [f"{label}{suffix}"]
        lines.extend(f"{symbol}: {value:,.4f}" for symbol, value in values)
        anchor_value = values[0][1]
        tooltip = self.price_axis.annotate(
            "\n".join(lines),
            xy=(timestamp, anchor_value),
            xytext=(12, 15),
            textcoords="offset points",
            color=TEXT,
            fontsize=9,
            bbox={"boxstyle": "round,pad=0.4", "facecolor": PANEL, "edgecolor": GRID},
            zorder=8,
        )
        self.hover_artists.append(tooltip)
        self.canvas.draw_idle()

    def _draw_volume_hover(self, x_position: float) -> None:
        self._clear_hover()
        position = _nearest_series_position(self.current_frame["Close"], x_position)
        timestamp = self.current_frame.index[position]
        guide = self.volume_axis.axvline(timestamp, color=MUTED, linewidth=0.8, linestyle="--")
        self.hover_artists.append(guide)
        label = self._format_chart_time(timestamp)
        symbol = self.selected_instrument.symbol if self.selected_instrument else ""
        if {"BuyCashEUR", "SellCashEUR"}.issubset(self.current_frame.columns):
            buy_cash = float(self.current_frame["BuyCashEUR"].iloc[position])
            sell_cash = float(self.current_frame["SellCashEUR"].iloc[position])
            y_anchor = buy_cash if buy_cash >= sell_cash else -sell_cash
            marker_value = y_anchor
            lines = [
                label,
                f"{symbol} trades",
                f"Buys: {format_euro_cash_value(buy_cash)}",
                f"Sells: {format_euro_cash_value(sell_cash)}",
                f"Net: {format_euro_cash_value(sell_cash - buy_cash)}",
            ]
            marker_color = UP if marker_value >= 0 else DOWN
        elif "Volume" in self.current_frame.columns:
            volume = float(self.current_frame["Volume"].iloc[position])
            y_anchor = volume
            marker_value = volume
            lines = [label, f"{symbol} volume: {format_volume_value(volume)}"]
            marker_color = SERIES_COLORS[0]
        else:
            return
        marker = self.volume_axis.scatter(
            [timestamp],
            [marker_value],
            color=marker_color,
            s=30,
            edgecolors=BG,
            zorder=7,
        )
        self.hover_artists.append(marker)
        tooltip = self.volume_axis.annotate(
            "\n".join(lines),
            xy=(timestamp, y_anchor),
            xytext=(12, 12),
            textcoords="offset points",
            color=TEXT,
            fontsize=9,
            bbox={"boxstyle": "round,pad=0.4", "facecolor": PANEL, "edgecolor": GRID},
            zorder=8,
        )
        self.hover_artists.append(tooltip)
        self.canvas.draw_idle()

    def _hide_hover(self, _event=None) -> None:
        if self.hover_artists:
            self._clear_hover()
            self.canvas.draw_idle()

    def _clear_hover(self) -> None:
        for artist in self.hover_artists:
            try:
                artist.remove()
            except ValueError:
                pass
        self.hover_artists = []

    def _zoom_to_rectangle(self, press_event, release_event) -> None:
        if self.measurement_mode or self.current_frame.empty:
            return
        if None in (press_event.xdata, press_event.ydata, release_event.xdata, release_event.ydata):
            return
        x_min, x_max = sorted((press_event.xdata, release_event.xdata))
        y_min, y_max = sorted((press_event.ydata, release_event.ydata))
        if x_min == x_max or y_min == y_max:
            return
        self.price_axis.set_xlim(x_min, x_max)
        self.price_axis.set_ylim(y_min, y_max)
        self._rescale_visible_volume(x_min, x_max)
        self.canvas.draw_idle()
        self.status_var.set("Zoomed selection | Right-click chart and choose Reset Zoom to restore.")

    def _install_zoom_selector(self) -> None:
        if hasattr(self, "zoom_selector"):
            self.zoom_selector.disconnect_events()
        self.zoom_selector = RectangleSelector(
            self.price_axis,
            self._zoom_to_rectangle,
            useblit=True,
            button=[MouseButton.LEFT],
            minspanx=5,
            minspany=5,
            spancoords="pixels",
            props={"facecolor": ORANGE, "edgecolor": ORANGE, "alpha": 0.18},
        )
        self.zoom_selector.set_active(
            not self.measurement_mode and not self.text_selection_dragging
        )

    def _reset_zoom(self) -> None:
        if self.current_frame.empty:
            return
        for axis in (self.price_axis, self.volume_axis, self.study_axis):
            axis.relim()
            axis.autoscale(enable=True, axis="both", tight=False)
        if self.technical_study and self.technical_study[0] == "RSI":
            self.study_axis.set_ylim(0, 100)
        self._apply_full_date_bounds()
        self._apply_full_price_bounds()
        self.canvas.draw_idle()
        self.status_var.set("Chart zoom reset.")

    def _apply_full_date_bounds(self) -> None:
        if not self.current_frames:
            return
        start, end = comparison_date_bounds(self.current_frames)
        self.price_axis.set_xlim(start, end)

    def _apply_full_price_bounds(self) -> None:
        if not self.current_frames:
            return
        displayed = displayed_close_series(self.current_frames, self.display_mode_var.get())
        lower, upper = comparison_price_bounds(displayed)
        self.price_axis.set_ylim(lower, upper)

    def _rescale_visible_volume(self, x_min: float, x_max: float) -> None:
        if "Volume" not in self.current_frame.columns:
            return
        dates = mdates.date2num(self.current_frame.index.to_pydatetime())
        visible_frame = self.current_frame.loc[(dates >= x_min) & (dates <= x_max)]
        if {"BuyCashEUR", "SellCashEUR"}.issubset(visible_frame.columns):
            buy_max = float(visible_frame["BuyCashEUR"].max()) if not visible_frame.empty else 0.0
            sell_max = float(visible_frame["SellCashEUR"].max()) if not visible_frame.empty else 0.0
            maximum = max(buy_max, sell_max)
            if maximum > 0:
                self.volume_axis.set_ylim(-maximum * 1.12, maximum * 1.12)
            return
        visible = visible_frame["Volume"]
        maximum = float(visible.max()) if not visible.empty else 0.0
        if maximum > 0:
            self.volume_axis.set_ylim(0, maximum * 1.08)

    def _start_return_measurement(self) -> None:
        if self.current_frame.empty:
            self.status_var.set("Load a chart before selecting return points.")
            return
        self._clear_measurement()
        self.measurement_mode = True
        self.zoom_selector.set_active(False)
        symbol = self.selected_instrument.symbol if self.selected_instrument else ""
        self.measurement_var.set(
            f"RETURN TOOL ({symbol}): click the starting dot, then the ending dot."
        )
        self.status_var.set(f"Select two closing-price dots for the primary series {symbol}.")

    def _capture_measurement_point(self, x_position: float) -> None:
        displayed_closes = displayed_close_series(
            {self.selected_instrument.symbol: self.current_frame}, self.display_mode_var.get()
        )[self.selected_instrument.symbol]
        index_position = min(
            range(len(displayed_closes.index)),
            key=lambda index: abs(
                mdates.date2num(displayed_closes.index[index].to_pydatetime()) - x_position
            ),
        )
        timestamp = displayed_closes.index[index_position]
        close = float(displayed_closes.iloc[index_position])
        self.measurement_points.append((timestamp, close))
        marker = self.price_axis.scatter(
            [timestamp], [close], color=ORANGE, s=42, zorder=5, edgecolors=BG
        )
        self.measurement_artists.append(marker)
        if len(self.measurement_points) == 1:
            self.measurement_var.set(
                f"RETURN TOOL: start {self._format_chart_time(timestamp)} @ {close:,.4f}; "
                "select ending dot."
            )
            self.canvas.draw_idle()
            return
        self._finish_return_measurement()

    def _finish_return_measurement(self) -> None:
        (start_time, start_close), (end_time, end_close) = self.measurement_points
        points_return, percent_return = calculate_return(start_close, end_close)
        color = UP if points_return >= 0 else DOWN
        line = self.price_axis.plot(
            [start_time, end_time],
            [start_close, end_close],
            color=color,
            linewidth=1.4,
            linestyle="--",
            marker="o",
            zorder=4,
        )[0]
        text = f"{points_return:+,.4f} pts | {percent_return:+.2f}%"
        note = self.price_axis.annotate(
            text,
            xy=(end_time, end_close),
            xytext=(10, 12),
            textcoords="offset points",
            color=TEXT,
            bbox={"boxstyle": "round,pad=0.35", "facecolor": PANEL, "edgecolor": color},
        )
        self.measurement_artists.extend([line, note])
        self.measurement_mode = False
        self.zoom_selector.set_active(not self.text_selection_dragging)
        self.measurement_var.set(
            f"RETURN: {self._format_chart_time(start_time)} to {self._format_chart_time(end_time)}"
            f" | Points {points_return:+,.4f} | Percent {percent_return:+.2f}%"
        )
        self.status_var.set("Return measurement complete. Right-click for chart options.")
        self.canvas.draw_idle()

    def _clear_measurement(self) -> None:
        for artist in self.measurement_artists:
            try:
                artist.remove()
            except ValueError:
                pass
        self.measurement_artists = []
        self.measurement_points = []
        self.measurement_mode = False
        self.measurement_var.set("")
        if hasattr(self, "zoom_selector"):
            self.zoom_selector.set_active(not self.text_selection_dragging)
        if hasattr(self, "canvas"):
            self.canvas.draw_idle()

    def _format_chart_time(self, timestamp: pd.Timestamp) -> str:
        if self.selected_range.interval.endswith("m"):
            return timestamp.strftime("%d %b %H:%M")
        return timestamp.strftime("%d %b %Y")

    def _configure_dates(self, range_spec: RangeSpec) -> None:
        if range_spec.interval.endswith("m"):
            self.volume_axis.xaxis.set_major_formatter(mdates.DateFormatter("%d %b\n%H:%M"))
        elif range_spec.period in {"max", "5y"}:
            self.volume_axis.xaxis.set_major_formatter(mdates.DateFormatter("%b\n%Y"))
        else:
            self.volume_axis.xaxis.set_major_formatter(mdates.DateFormatter("%d %b\n%Y"))
        self.figure.autofmt_xdate(rotation=0, ha="center")

    def _clear_chart(self, text: str) -> None:
        self.current_frame = pd.DataFrame()
        self.current_frames = {}
        self._clear_hover()
        self._clear_measurement()
        self.price_axis.clear()
        self.volume_axis.clear()
        self.study_axis.clear()
        self._style_axes()
        self._install_zoom_selector()
        self.price_axis.text(
            0.5,
            0.5,
            text,
            transform=self.price_axis.transAxes,
            color=MUTED,
            ha="center",
            va="center",
        )
        self.canvas.draw_idle()

    def _style_axes(self) -> None:
        for axis in (self.price_axis, self.volume_axis):
            axis.set_facecolor(BG)
            axis.tick_params(colors=MUTED, labelsize=9)
            axis.grid(True, color=GRID, linewidth=0.55, alpha=0.8)
            for spine in axis.spines.values():
                spine.set_color(GRID)
        self.study_axis.set_visible(False)
        self.study_axis.tick_params(colors=ORANGE, labelsize=9)
        self.study_axis.spines["right"].set_color(ORANGE)
        for spine_name in ("left", "top", "bottom"):
            self.study_axis.spines[spine_name].set_visible(False)
        self.study_axis.grid(False)
        self.price_axis.tick_params(labelbottom=False)

    def _run_background(self, function, on_success, label: str, is_current=lambda: True) -> None:
        def worker() -> None:
            try:
                result = function()
            except Exception as exc:  # network/provider errors belong in the status bar.
                error = exc
                self.after(
                    0,
                    lambda: self._show_error(label, error) if is_current() else None,
                )
                return
            self.after(0, lambda: on_success(result))

        threading.Thread(target=worker, daemon=True).start()

    def _show_error(self, label: str, exc: Exception) -> None:
        detail = str(exc).strip() or exc.__class__.__name__
        self.status_var.set(f"{label}: {detail}")
        messagebox.showerror(label, detail, parent=self)


def source_file_snapshot(paths: tuple[Path, ...]) -> tuple[tuple[str, int, int], ...]:
    snapshot = []
    for path in paths:
        try:
            metadata = path.stat()
            snapshot.append((str(path), metadata.st_mtime_ns, metadata.st_size))
        except FileNotFoundError:
            snapshot.append((str(path), -1, -1))
    return tuple(snapshot)


def load_window_geometry(path: Path) -> str | None:
    state = load_window_state(path)
    return state["geometry"] if path.exists() else None


def load_window_state(path: Path) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"geometry": DEFAULT_WINDOW_GEOMETRY, "state": "normal"}
    geometry = payload.get("geometry") if isinstance(payload, dict) else None
    if not isinstance(geometry, str) or parse_window_geometry(geometry) is None:
        geometry = DEFAULT_WINDOW_GEOMETRY
    state = payload.get("state", "normal") if isinstance(payload, dict) else "normal"
    if state not in {"normal", "zoomed"}:
        state = "normal"
    return {"geometry": geometry, "state": state}


def load_watchlist_state(path: Path) -> list[dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    if not isinstance(payload, list):
        return []
    return [row for row in payload if isinstance(row, dict)]


def load_layout_state(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def save_layout_state(path: Path, layout: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(layout, indent=2), encoding="utf-8")


def window_place_geometry(widget: tk.Widget) -> dict[str, int]:
    return {
        "x": int(widget.winfo_x()),
        "y": int(widget.winfo_y()),
        "width": int(widget.winfo_width()),
        "height": int(widget.winfo_height()),
    }


def save_watchlist_state(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2), encoding="utf-8")


def watchlist_row_from_instrument(instrument: Instrument | None) -> dict:
    if instrument is None:
        return {}
    return {
        "symbol": instrument.symbol,
        "name": instrument.name,
        "exchange": instrument.exchange,
        "quote_type": instrument.quote_type,
        "currency": instrument.currency,
        "source": instrument.source,
        "figi": instrument.figi,
        "market_cap": instrument.market_cap,
        "aum": instrument.aum,
        "isin": instrument.isin,
    }


def instrument_from_watchlist_row(row: dict) -> Instrument | None:
    symbol = str(row.get("symbol", "")).strip()
    if not symbol:
        return None
    return Instrument(
        symbol=symbol,
        name=str(row.get("name", "") or symbol),
        exchange=str(row.get("exchange", "") or ""),
        quote_type=str(row.get("quote_type", "") or ""),
        currency=str(row.get("currency", "") or ""),
        source=str(row.get("source", "") or "Yahoo Finance"),
        figi=str(row.get("figi", "") or ""),
        market_cap=_optional_float_from_json(row.get("market_cap")),
        aum=_optional_float_from_json(row.get("aum")),
        isin=str(row.get("isin", "") or ""),
    )


def _optional_float_from_json(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def save_window_geometry(path: Path, geometry: str, window_state: str = "normal") -> None:
    if parse_window_geometry(geometry) is None:
        return
    if window_state not in {"normal", "zoomed"}:
        window_state = "normal"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"geometry": geometry, "state": window_state}),
        encoding="utf-8",
    )


def parse_window_geometry(geometry: str) -> tuple[int, int, int, int] | None:
    match = re.fullmatch(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)", geometry.strip())
    if not match:
        return None
    return tuple(int(value) for value in match.groups())


def constrain_window_geometry(geometry: str, screen_width: int, screen_height: int) -> str:
    parsed = parse_window_geometry(geometry)
    if parsed is None:
        return DEFAULT_WINDOW_GEOMETRY
    return geometry


def _bar_width(frame: pd.DataFrame) -> float:
    if len(frame.index) < 2:
        return 0.03
    deltas = frame.index.to_series().diff().dropna()
    return float(deltas.median().total_seconds() / (24 * 60 * 60) * 0.72)


def calculate_return(start_price: float, end_price: float) -> tuple[float, float]:
    points_return = end_price - start_price
    percent_return = (points_return / start_price * 100) if start_price else 0.0
    return points_return, percent_return


def technical_study_label(study: tuple[str, int] | None) -> str:
    if not study:
        return "Volume"
    name, period = study
    return f"{name} {period}"


def technical_indicator(closes: pd.Series, study: tuple[str, int]) -> pd.Series:
    name, period = study
    if name == "MOM":
        return closes.pct_change(periods=period, fill_method=None) * 100
    changes = closes.diff()
    if name == "SIGMA":
        return (
            closes.pct_change(fill_method=None).rolling(period, min_periods=period).std(ddof=0)
            * 100
        )
    if name == "RSI":
        gains = changes.clip(lower=0).ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        losses = (
            -changes.clip(upper=0).ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        )
        relative_strength = gains / losses
        rsi = 100 - (100 / (1 + relative_strength))
        return rsi.mask((gains == 0) & (losses == 0), 50).mask((gains > 0) & (losses == 0), 100)
    raise ValueError(f"Unsupported technical study: {name}")


def custom_range_spec(mode: str, start: str, end: str, interval: str) -> RangeSpec:
    start = start.strip()
    end = end.strip()
    try:
        start_date = pd.Timestamp(start)
        end_date = pd.Timestamp(end)
    except (TypeError, ValueError):
        raise ValueError("Custom date range must use YYYY-MM-DD dates.") from None
    if not start or not end or start_date.strftime("%Y-%m-%d") != start or end_date.strftime(
        "%Y-%m-%d"
    ) != end:
        raise ValueError("Custom date range must use YYYY-MM-DD dates.")
    if start_date > end_date:
        raise ValueError("Custom date range start must not be after end.")
    if mode == "Intraday" and interval not in {"1m", "5m", "15m", "30m", "60m"}:
        raise ValueError("Choose a supported intraday bar size.")
    if mode not in {"Intraday", "Historical"}:
        raise ValueError("Unsupported custom range mode.")
    suffix = f" / {interval}" if mode == "Intraday" else ""
    return RangeSpec(f"{start}..{end}{suffix}", "custom", interval, start, end)


def normalized_rectangle(
    start: tuple[int, int], end: tuple[int, int]
) -> tuple[int, int, int, int]:
    return (
        min(start[0], end[0]),
        min(start[1], end[1]),
        max(start[0], end[0]),
        max(start[1], end[1]),
    )


def rectangles_intersect(
    first: tuple[int, int, int, int], second: tuple[int, int, int, int]
) -> bool:
    return not (
        first[2] < second[0]
        or second[2] < first[0]
        or first[3] < second[1]
        or second[3] < first[1]
    )


def rectangle_is_drag(bounds: tuple[int, int, int, int], minimum_span: int = 5) -> bool:
    return bounds[2] - bounds[0] >= minimum_span or bounds[3] - bounds[1] >= minimum_span


def widget_screen_bounds(widget) -> tuple[int, int, int, int]:
    left = widget.winfo_rootx()
    top = widget.winfo_rooty()
    return left, top, left + widget.winfo_width(), top + widget.winfo_height()


def walk_visible_widgets(widget):
    for child in widget.winfo_children():
        if not child.winfo_viewable():
            continue
        yield child
        yield from walk_visible_widgets(child)


def displayed_widget_text(widget) -> str:
    if isinstance(widget, tk.Entry):
        return widget.get().strip()
    if not isinstance(
        widget,
        (tk.Label, tk.Button, ttk.Label, ttk.Button, ttk.Checkbutton),
    ):
        return ""
    variable = str(widget.cget("textvariable")) if "textvariable" in widget.keys() else ""
    if variable:
        return str(widget.getvar(variable)).strip()
    return str(widget.cget("text")).strip()


def displayed_text_bounds(widget) -> tuple[int, int, int, int]:
    text = displayed_widget_text(widget)
    if not text:
        return (0, 0, -1, -1)
    font = _widget_font(widget)
    lines = text.splitlines() or [text]
    width = max(font.measure(line) for line in lines)
    height = font.metrics("linespace") * len(lines)
    anchor = str(widget.cget("anchor") or "w") if "anchor" in widget.keys() else "w"
    return anchored_text_bounds(widget_screen_bounds(widget), (width, height), anchor)


def anchored_text_bounds(
    widget_bounds: tuple[int, int, int, int],
    text_size: tuple[int, int],
    anchor: str = "w",
) -> tuple[int, int, int, int]:
    widget_left, widget_top, widget_right, widget_bottom = widget_bounds
    width, height = text_size
    if "e" in anchor:
        left = widget_right - width
    elif "w" in anchor:
        left = widget_left
    else:
        left = widget_left + max((widget_right - widget_left - width) // 2, 0)
    if "n" in anchor:
        top = widget_top
    elif "s" in anchor:
        top = widget_bottom - height
    else:
        top = widget_top + max((widget_bottom - widget_top - height) // 2, 0)
    return left, top, left + width, top + height


def _widget_font(widget):
    font_name = str(widget.cget("font")) if "font" in widget.keys() else ""
    if not font_name and isinstance(widget, ttk.Widget):
        style_name = str(widget.cget("style") or widget.winfo_class())
        font_name = str(ttk.Style(widget).lookup(style_name, "font"))
    return tkfont.Font(root=widget, font=font_name) if font_name else tkfont.nametofont("TkDefaultFont")


def tree_text_blocks(
    tree: ttk.Treeview, bounds: tuple[int, int, int, int]
) -> list[tuple[int, int, str]]:
    blocks: list[tuple[int, int, str]] = []
    left = tree.winfo_rootx()
    top = tree.winfo_rooty()
    columns = tuple(tree["columns"])
    items = tree.get_children()
    first_row = tree.bbox(items[0]) if items else ()
    heading_height = first_row[1] if first_row else 25
    heading_bounds = (left, top, left + tree.winfo_width(), top + heading_height)
    if rectangles_intersect(heading_bounds, bounds):
        headings = [str(tree.heading(column, "text")).strip() for column in columns]
        blocks.append((top, left, "\t".join(heading for heading in headings if heading)))
    for item in items:
        item_bounds = tree.bbox(item)
        if not item_bounds:
            continue
        x, y, width, height = item_bounds
        row_bounds = (left + x, top + y, left + x + width, top + y + height)
        if rectangles_intersect(row_bounds, bounds):
            values = [str(value).strip() for value in tree.item(item, "values")]
            blocks.append((top + y, left + x, "\t".join(values)))
    return blocks


def tree_text_at_point(tree: ttk.Treeview, x: int, y: int) -> bool:
    point = (x, y, x, y)
    left = tree.winfo_rootx()
    top = tree.winfo_rooty()
    font = _widget_font(tree)
    items = tree.get_children()
    first_row = tree.bbox(items[0]) if items else ()
    heading_height = first_row[1] if first_row else 25
    column_left = left
    for column in tuple(tree["columns"]):
        column_width = int(tree.column(column, "width"))
        heading = str(tree.heading(column, "text")).strip()
        if heading and rectangles_intersect(
            (column_left + 5, top, column_left + 5 + font.measure(heading), top + heading_height),
            point,
        ):
            return True
        column_left += column_width
    for item in items:
        item_box = tree.bbox(item)
        if not item_box:
            continue
        row_left, row_top, _row_width, row_height = item_box
        column_left = left + row_left
        for column, value in zip(tuple(tree["columns"]), tree.item(item, "values")):
            text = str(value).strip()
            column_width = int(tree.column(column, "width"))
            if text and rectangles_intersect(
                (
                    column_left + 5,
                    top + row_top,
                    column_left + 5 + font.measure(text),
                    top + row_top + row_height,
                ),
                point,
            ):
                return True
            column_left += column_width
    return False


def ordered_text_blocks(blocks: list[tuple[int, int, str]]) -> str:
    lines: list[str] = []
    for _top, _left, text in sorted(blocks):
        if text and (not lines or text != lines[-1]):
            lines.append(text)
    return "\n".join(lines)


def filter_and_sort_instruments(
    instruments: list[Instrument], sort_mode: str, exchange_filter: str
) -> list[Instrument]:
    filtered = [
        instrument
        for instrument in instruments
        if exchange_filter == "All Markets" or instrument.exchange == exchange_filter
    ]
    if sort_mode == "Market Cap":
        return sorted(
            filtered,
            key=lambda instrument: (
                instrument.market_cap is None,
                -(instrument.market_cap or 0.0),
            ),
        )
    if sort_mode == "Exchange":
        return sorted(
            filtered,
            key=lambda instrument: (instrument.exchange.casefold(), instrument.symbol.casefold()),
        )
    return filtered


def instrument_identity_text(instrument: Instrument) -> str:
    isin = instrument.isin or "N/A"
    asset_type = instrument.quote_type or "N/A"
    return f"ISIN: {isin}  |  Asset Type: {asset_type}"


def instrument_fundamentals_text(instrument: Instrument) -> str:
    quote_type = instrument.quote_type.upper()
    if quote_type == "PORTFOLIO INDEX":
        return f"Portfolio Value: {format_currency_size(instrument.aum or instrument.market_cap)} EUR"
    if quote_type in {"ETF", "MUTUALFUND", "FUND"}:
        return (
            f"Market Cap: {format_currency_size(instrument.market_cap)}"
            f"  |  AUM: {format_currency_size(instrument.aum)}"
        )
    if quote_type in {"EQUITY", "STOCK"}:
        return f"Market Cap: {format_currency_size(instrument.market_cap)}"
    if instrument.market_cap is not None:
        return f"Market Cap: {format_currency_size(instrument.market_cap)}"
    return ""


def watchlist_asset_label(instrument: Instrument) -> str:
    return f"{instrument.symbol}  {instrument.name}".strip()


def format_quote_value(value: float | None) -> str:
    if value is None:
        return ""
    if abs(value) >= 1_000:
        return f"{value:,.2f}"
    if abs(value) >= 10:
        return f"{value:,.3f}"
    return f"{value:,.4f}"


def format_market_cap(market_cap: float | None) -> str:
    if market_cap is None:
        return ""
    return format_compact_number(market_cap)


def format_currency_size(value: float | None) -> str:
    if value is None:
        return "N/A"
    return format_compact_number(value)


def format_compact_number(value: float) -> str:
    for size, suffix in ((1_000_000_000_000, "T"), (1_000_000_000, "B"), (1_000_000, "M")):
        if abs(value) >= size:
            return f"{value / size:.1f}{suffix}"
    return f"{value:,.0f}"


def fit_popup_to_window(
    *,
    anchor_x: int,
    anchor_y: int,
    anchor_width: int,
    anchor_height: int,
    preferred_width: int,
    popup_height: int,
    window_left: int,
    window_right: int,
    window_top: int,
    window_bottom: int,
    align_right: bool = False,
) -> tuple[int, int, int]:
    available_width = max(window_right - window_left, 1)
    width = min(preferred_width, available_width)
    preferred_x = anchor_x + anchor_width - width if align_right else anchor_x
    x = max(window_left, min(preferred_x, window_right - width))
    below_y = anchor_y + anchor_height + 3
    above_y = anchor_y - popup_height - 3
    y = above_y if below_y + popup_height > window_bottom else below_y
    y = max(window_top, min(y, window_bottom - popup_height))
    return x, y, width


def chart_date_bounds(frame: pd.DataFrame) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = frame.index[0]
    end = frame.index[-1]
    if start == end:
        end = end + pd.Timedelta(days=1)
    return start, end


def prepare_comparison_frames(
    frames: dict[str, pd.DataFrame], range_spec: RangeSpec
) -> dict[str, pd.DataFrame]:
    visible = {
        symbol: normalize_chart_frame_index(frame)
        for symbol, frame in frames.items()
        if not frame.empty
    }
    if range_spec.period != "max" or len(visible) < 2:
        return visible
    shared_start = max(frame.index[0] for frame in visible.values())
    return {
        symbol: frame.loc[frame.index >= shared_start]
        for symbol, frame in visible.items()
        if not frame.loc[frame.index >= shared_start].empty
    }


def normalize_chart_frame_index(frame: pd.DataFrame) -> pd.DataFrame:
    index = pd.to_datetime(frame.index)
    if getattr(index, "tz", None) is not None:
        index = index.tz_localize(None)
    if len(index) and all(timestamp.time() == datetime.min.time() for timestamp in index):
        index = index.normalize()
    result = frame.copy()
    result.index = index
    result.attrs.update(frame.attrs)
    return result


def displayed_close_series(
    frames: dict[str, pd.DataFrame], display_mode: str
) -> dict[str, pd.Series]:
    closes = {symbol: frame["Close"] for symbol, frame in frames.items()}
    if display_mode != "Rebased 100":
        return closes
    return {
        symbol: series / float(series.iloc[0]) * 100 if float(series.iloc[0]) else series * 0
        for symbol, series in closes.items()
    }


def calculate_beta_model(
    frames: dict[str, pd.DataFrame], symbols: list[str]
) -> BetaModelStats | None:
    if len(symbols) < 2 or any(symbol not in frames for symbol in symbols):
        return None
    aligned_closes = pd.concat(
        [frames[symbol]["Close"].rename(symbol) for symbol in symbols],
        axis=1,
        join="inner",
    ).dropna()
    returns = aligned_closes.pct_change(fill_method=None).dropna()
    parameter_count = len(symbols)
    if len(returns) <= parameter_count:
        return None
    y = returns[symbols[0]].to_numpy(dtype=float)
    x = returns[symbols[1:]].to_numpy(dtype=float)
    design = np.column_stack((np.ones(len(returns)), x))
    if np.linalg.matrix_rank(design) < parameter_count:
        return None
    estimates, _residuals, _rank, _values = np.linalg.lstsq(design, y, rcond=None)
    fitted = design @ estimates
    errors = y - fitted
    residual_sum_squares = float(errors @ errors)
    total_sum_squares = float(((y - y.mean()) ** 2).sum())
    degrees_of_freedom = len(returns) - parameter_count
    residual_variance = residual_sum_squares / degrees_of_freedom
    covariance = residual_variance * np.linalg.inv(design.T @ design)
    standard_errors = np.sqrt(np.maximum(np.diag(covariance), 0.0))
    coefficients = [
        _ols_coefficient(float(estimate), float(error), degrees_of_freedom)
        for estimate, error in zip(estimates, standard_errors)
    ]
    r_squared = (
        1.0 - residual_sum_squares / total_sum_squares
        if total_sum_squares > 0
        else 0.0
    )
    adjusted_r_squared = 1.0 - (
        (1.0 - r_squared) * (len(returns) - 1) / degrees_of_freedom
    )
    return BetaModelStats(
        y_symbol=symbols[0],
        observations=len(returns),
        r_squared=r_squared,
        adjusted_r_squared=adjusted_r_squared,
        alpha=coefficients[0],
        betas=dict(zip(symbols[1:], coefficients[1:])),
    )


def _ols_coefficient(
    estimate: float, standard_error: float, degrees_of_freedom: int
) -> OlsCoefficient:
    if standard_error == 0.0:
        t_stat = math.copysign(math.inf, estimate) if estimate else 0.0
    else:
        t_stat = estimate / standard_error
    return OlsCoefficient(
        estimate=estimate,
        std_error=standard_error,
        t_stat=t_stat,
        p_value=_student_t_two_sided_p_value(t_stat, degrees_of_freedom),
    )


def _student_t_two_sided_p_value(t_stat: float, degrees_of_freedom: int) -> float:
    if degrees_of_freedom <= 0:
        return float("nan")
    if math.isinf(t_stat):
        return 0.0
    x = degrees_of_freedom / (degrees_of_freedom + t_stat * t_stat)
    return _regularized_incomplete_beta(x, degrees_of_freedom / 2.0, 0.5)


def _regularized_incomplete_beta(x: float, a: float, b: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    factor = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + a * math.log(x)
        + b * math.log1p(-x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return factor * _beta_continued_fraction(x, a, b) / a
    return 1.0 - factor * _beta_continued_fraction(1.0 - x, b, a) / b


def _beta_continued_fraction(x: float, a: float, b: float) -> float:
    maximum_iterations = 200
    epsilon = 3e-12
    minimum = 1e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    d = 1.0 / max(abs(d), minimum) * (1 if d >= 0 else -1)
    result = d
    for iteration in range(1, maximum_iterations + 1):
        twice = 2 * iteration
        numerator = iteration * (b - iteration) * x / ((qam + twice) * (a + twice))
        d = 1.0 + numerator * d
        if abs(d) < minimum:
            d = minimum
        c = 1.0 + numerator / c
        if abs(c) < minimum:
            c = minimum
        d = 1.0 / d
        result *= d * c
        numerator = -(a + iteration) * (qab + iteration) * x / (
            (a + twice) * (qap + twice)
        )
        d = 1.0 + numerator * d
        if abs(d) < minimum:
            d = minimum
        c = 1.0 + numerator / c
        if abs(c) < minimum:
            c = minimum
        d = 1.0 / d
        update = d * c
        result *= update
        if abs(update - 1.0) < epsilon:
            break
    return result


def format_probability(value: float) -> str:
    if math.isnan(value):
        return "-"
    return "<.001" if value < 0.001 else f"{value:.3f}"


def nearest_displayed_values(
    series: dict[str, pd.Series], x_position: float
) -> tuple[pd.Timestamp, list[tuple[str, float]]]:
    first_series = next(iter(series.values()))
    primary_position = _nearest_series_position(first_series, x_position)
    timestamp = first_series.index[primary_position]
    guide_position = mdates.date2num(timestamp.to_pydatetime())
    values = []
    for symbol, closes in series.items():
        position = _nearest_series_position(closes, guide_position)
        values.append((symbol, float(closes.iloc[position])))
    return timestamp, values


def _nearest_series_position(series: pd.Series, x_position: float) -> int:
    return min(
        range(len(series.index)),
        key=lambda index: abs(mdates.date2num(series.index[index].to_pydatetime()) - x_position),
    )


def comparison_date_bounds(frames: dict[str, pd.DataFrame]) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = min(frame.index[0] for frame in frames.values())
    end = max(frame.index[-1] for frame in frames.values())
    if start == end:
        end = end + pd.Timedelta(days=1)
    return start, end


def comparison_price_bounds(series: dict[str, pd.Series]) -> tuple[float, float]:
    values = pd.concat(series.values()).dropna()
    low = float(values.min())
    high = float(values.max())
    spread = high - low
    padding = spread * 0.08 if spread else max(abs(low) * 0.005, 0.01)
    return low - padding, high + padding


def _display_data_source(frame: pd.DataFrame) -> str:
    source = frame.attrs.get("data_source", "Unknown source")
    quality = frame.attrs.get("quality")
    return f"{source} ({quality.score:.0f}/100)" if quality else source


def _volume_formatter():
    from matplotlib.ticker import FuncFormatter

    return FuncFormatter(lambda value, _position: format_volume_value(value))


def format_volume_value(value: float) -> str:
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f}B"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.0f}K"
    return f"{value:.0f}"


def _euro_cash_formatter():
    from matplotlib.ticker import FuncFormatter

    return FuncFormatter(lambda value, _position: format_euro_cash_value(value))


def format_euro_cash_value(value: float, compact: bool = False) -> str:
    sign = "-" if value < 0 else ""
    absolute = abs(value)
    if absolute >= 1_000_000:
        return f"{sign}EUR {absolute / 1_000_000:.1f}M"
    if absolute >= 1_000:
        decimals = 1 if compact and absolute < 10_000 else 0
        return f"{sign}EUR {absolute / 1_000:.{decimals}f}K"
    return f"{sign}EUR {absolute:.0f}"


def _technical_formatter():
    from matplotlib.ticker import FuncFormatter

    return FuncFormatter(lambda value, _position: f"{value:.1f}")


def main() -> None:
    app = MarketTerminalApp()
    app.mainloop()


if __name__ == "__main__":
    main()
