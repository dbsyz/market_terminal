from __future__ import annotations

import json
import math
import os
import re
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from tkinter import messagebox, simpledialog, ttk
from zoneinfo import ZoneInfo

import matplotlib.dates as mdates
import numpy as np
import pandas as pd
from matplotlib.backend_bases import MouseButton
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from matplotlib.widgets import RectangleSelector

from .models import (
    HISTORICAL_RANGES,
    INTRADAY_MATRIX,
    INTRADAY_RANGES,
    Instrument,
    MarketEvent,
    MarketSession,
    RangeSpec,
)
from .macro_dashboard import MacroDashboardService, MacroDashboardSnapshot
from .news_feed import (
    GdeltNewsClient,
    GdeltRateLimitError,
    NewsArticle,
    default_news_queries,
    news_query_by_label,
)
from .portfolio_index import (
    PORTFOLIO_INDEX_SYMBOL,
    PortfolioConstituentQuote,
    portfolio_constituent_quotes,
)
from .providers import MarketDataProvider
from .sec_edgar import SecCompanyContext, SecEdgarClient, format_sec_company_context


BG = "#000000"
PANEL = "#17212b"
GRID = "#30404d"
ORANGE = "#f6a400"
TEXT = "#e8edf2"
MUTED = "#a7b3be"
UP = "#38c172"
DOWN = "#ef5350"
WATCHLIST_ROW_EVEN = "#10161c"
WATCHLIST_ROW_ODD = "#1b242c"
TERMINAL_FONT_FAMILY = "Cascadia Mono"
MAXIMIZE_ICON = "\u25a1"
TITLEBAR_HEIGHT = 28
TITLEBAR_TITLE_FONT = (TERMINAL_FONT_FAMILY, 9, "bold")
TITLEBAR_BUTTON_FONT = (TERMINAL_FONT_FAMILY, 8, "bold")
TITLEBAR_BUTTON_WIDTH = 3
TITLEBAR_MENU_WIDTH = 8
TITLEBAR_BUTTON_PADX = 4
TITLEBAR_BUTTON_PADY = 1
WATCHLIST_COLUMNS = (
    ("asset", "Asset", 125),
    ("last", "Last", 70),
    ("bid", "Bid", 70),
    ("ask", "Ask", 70),
    ("change", "Chg", 80),
    ("volume", "Volume", 75),
    ("latency", "Latency", 65),
)
WATCHLIST_MIN_COLUMN_WIDTH = 35
BETA_SERIES_COLUMNS = ("symbol", "beta", "stderr", "tstat", "pvalue")
BETA_SERIES_HEADINGS = {
    "symbol": "Series",
    "beta": "Beta",
    "stderr": "SE",
    "tstat": "t",
    "pvalue": "p",
}
BETA_SERIES_MIN_COLUMN_WIDTH = 46
BETA_SERIES_MAX_COLUMN_WIDTH = 74
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
    "live_quotes.py",
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
MIN_MACRO_WINDOW_WIDTH = 520
MIN_MACRO_WINDOW_HEIGHT = 320
MIN_NEWS_WINDOW_WIDTH = 620
MIN_NEWS_WINDOW_HEIGHT = 360
MIN_EVENT_WINDOW_WIDTH = 560
MIN_EVENT_WINDOW_HEIGHT = 280
SHOW_MACRO_WINDOW = False
SHOW_NEWS_WINDOW = False
SHOW_EVENT_WINDOW = True
WATCHLIST_REFRESH_INTERVAL_MS = 120000
WATCHLIST_REFRESH_STAGGER_MS = 2500
WATCHLIST_PRIORITY_PAUSE_MS = 20000
WATCHLIST_CLOSED_REFRESH_INTERVAL_MS = 1800000
EVENT_GROUP_LOAD_DELAY_MS = 3500
WATCHLIST_TICK_FLASH_MS = 900
CHART_LIVE_QUOTE_INTERVAL_MS = 3000
CHART_TOP_OVERLAY_Y = 6
CHART_HEADER_LINE_GAP = 21
PRICE_RENDER_MODES = (
    "bars",
    "candles",
    "hollow_candles",
    "hlc_bars",
    "line",
    "line_markers",
    "step_line",
)
PRICE_RENDER_LABELS = {
    "bars": "BAR",
    "candles": "CND",
    "hollow_candles": "HOL",
    "hlc_bars": "HLC",
    "line": "LIN",
    "line_markers": "MRK",
    "step_line": "STP",
}
PRICE_RENDER_DESCRIPTIONS = {
    "bars": "OHLC bars",
    "candles": "candles",
    "hollow_candles": "hollow candles",
    "hlc_bars": "HLC bars",
    "line": "line",
    "line_markers": "line with markers",
    "step_line": "step line",
}


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


class ButtonTooltip:
    def __init__(self, app: "MarketTerminalApp") -> None:
        self.app = app
        self.window: tk.Toplevel | None = None
        self.after_id: str | None = None
        self.widget: tk.Widget | None = None

    def schedule(self, widget: tk.Widget) -> None:
        text = self.app._button_tooltip_text(widget)
        if not text:
            return
        self.cancel()
        self.widget = widget
        self.after_id = self.app.after(450, lambda: self.show(widget, text))

    def cancel(self) -> None:
        if self.after_id:
            self.app.after_cancel(self.after_id)
            self.after_id = None
        self.hide()

    def show(self, widget: tk.Widget, text: str) -> None:
        self.after_id = None
        if not widget.winfo_exists():
            return
        self.hide()
        self.window = tk.Toplevel(self.app)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(bg=ORANGE)
        label = tk.Label(
            self.window,
            text=text,
            bg=PANEL,
            fg=TEXT,
            font=(TERMINAL_FONT_FAMILY, 9),
            padx=8,
            pady=5,
            justify=tk.LEFT,
        )
        label.pack(padx=1, pady=1)
        x = widget.winfo_rootx()
        y = widget.winfo_rooty() + widget.winfo_height() + 5
        self.window.geometry(f"+{x}+{y}")
        self.window.deiconify()
        self.window.lift()

    def hide(self) -> None:
        if self.window is not None:
            self.window.destroy()
            self.window = None


class MarketTerminalApp(tk.Tk):
    def __init__(self, provider: MarketDataProvider | None = None) -> None:
        super().__init__()
        self.provider = provider or MarketDataProvider()
        self.macro_service = MacroDashboardService()
        self.news_client = GdeltNewsClient()
        self.title("Market Terminal | Price Charts")
        self.minsize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        self.configure(bg=BG)
        state_root = Path(__file__).resolve().parent / "out"
        self.sec_client = SecEdgarClient(cache_dir=state_root / "sec_cache")
        self.sec_context: SecCompanyContext | None = None
        self.window_state_path = state_root / "window_state.json"
        self.layout_state_path = state_root / "layout.json"
        self.watchlist_state_path = state_root / "watchlist.json"
        self.saved_window_state = load_window_state(self.window_state_path)
        self.saved_layout_state = load_layout_state(self.layout_state_path)
        self.saved_settings = normalized_app_settings(self.saved_layout_state.get("settings"))
        self.startup_layout_state = json.loads(json.dumps(self.saved_layout_state))
        self.saved_watchlist_state = load_watchlist_state(self.watchlist_state_path)
        self.geometry(self.saved_window_state["geometry"])

        self.search_var = tk.StringVar(value="")
        self.compare_search_var = tk.StringVar(value="")
        self.watchlist_search_var = tk.StringVar(value="")
        self.event_search_var = tk.StringVar(value="")
        self.mode_var = tk.StringVar(value=self.saved_settings["chart_mode"])
        self.status_var = tk.StringVar(
            value="Public/delayed market data via Yahoo Finance | Identifier mapping via OpenFIGI"
        )
        self.quote_var = tk.StringVar(value="")
        self.chart_header_summary_var = tk.StringVar(value="")
        self.fundamentals_var = tk.StringVar(value="")
        self.sec_context_var = tk.StringVar(value="")
        self.identity_var = tk.StringVar(value="")
        self.measurement_var = tk.StringVar(value="")
        self.session_var = tk.StringVar(value="")
        self.hours_var = tk.StringVar(value="")
        self.extended_hours_var = tk.BooleanVar(value=self.saved_settings["extended_hours"])
        self.display_mode_var = tk.StringVar(value=self.saved_settings["display_mode"])
        self.price_render_mode = self.saved_settings["price_render_mode"]
        self.compare_visible_var = tk.BooleanVar(value=self.saved_settings["compare_panel_visible"])
        self.rebase_comparison_var = tk.BooleanVar(value=self.saved_settings["rebase_comparison"])
        self.betas_comparison_var = tk.BooleanVar(value=self.saved_settings["betas_comparison"])
        self.technical_study: tuple[str, int] | None = self.saved_settings["technical_study"]
        self.intraday_start_var = tk.StringVar(value="")
        self.intraday_end_var = tk.StringVar(value="")
        self.intraday_custom_bar_var = tk.StringVar(value=self.saved_settings["intraday_custom_bar"])
        self.historical_start_var = tk.StringVar(value="")
        self.historical_end_var = tk.StringVar(value="")
        self.chart_group_var = tk.StringVar(value=self.saved_settings["chart_group"])
        self.watchlist_group_var = tk.StringVar(value=self.saved_settings["watchlist_group"])
        self.event_group_var = tk.StringVar(value=self.saved_settings["event_group"])
        self.macro_category_var = tk.StringVar(value=self.saved_settings["macro_category"])
        self.macro_status_var = tk.StringVar(value="FRED macro dashboard")
        self.news_topic_var = tk.StringVar(value=self.saved_settings["news_topic"])
        self.news_status_var = tk.StringVar(value="Live news via GDELT. Click REFRESH to load.")
        self.event_status_var = tk.StringVar(value="Select a grouped watchlist stock or search.")
        self.local_time_var = tk.StringVar(value="")
        self.new_york_time_var = tk.StringVar(value="")
        self.london_time_var = tk.StringVar(value="")
        self.hong_kong_time_var = tk.StringVar(value="")
        self.search_action_var = tk.StringVar(value="OPEN SECURITY")
        self.search_sort_var = tk.StringVar(value=self.saved_settings["search_sort"])
        self.exchange_filter_var = tk.StringVar(value="All Markets")
        self.results: list[Instrument] = []
        self.raw_results: list[Instrument] = []
        self.chart_instruments: list[Instrument] = list(self.saved_settings["chart_instruments"])
        self.watchlist_instruments: dict[str, Instrument] = {}
        self.watchlist_target_item: str | None = None
        self.watchlist_editor: tk.Entry | None = None
        self.watchlist_quote_inflight: set[str] = set()
        self.watchlist_refresh_after_id: str | None = None
        self.watchlist_item_refresh_after_ids: dict[str, str] = {}
        self.watchlist_save_after_id: str | None = None
        self.event_group_load_after_id: str | None = None
        self.watchlist_next_refresh_at: dict[str, float] = {}
        self.watchlist_last_quotes: dict[str, tuple[float | None, float | None, float | None]] = {}
        self.watchlist_tick_reset_after_ids: dict[str, str] = {}
        self.watchlist_drag_item: str | None = None
        self.watchlist_drag_start_y = 0
        self.watchlist_drag_active = False
        self.watchlist_next_row_id = 1
        self.watchlist_context_item: str | None = None
        self.watchlist_groups: dict[str, str] = {}
        self.event_instrument: Instrument | None = None
        self.event_search_update_internal = False
        self.add_to_compare_mode = False
        self.suggestion_anchor = None
        self.selected_instrument: Instrument | None = (
            self.chart_instruments[0] if self.chart_instruments else None
        )
        self.selected_range = self.saved_settings["selected_range"]
        self.range_buttons: list[ttk.Button] = []
        self.technical_buttons: list[tuple[ttk.Button, tuple[str, int] | None]] = []
        self.range_popup_mode: str | None = None
        self.range_hide_after_id: str | None = None
        self.search_request_id = 0
        self.search_after_id: str | None = None
        self.chart_request_id = 0
        self.chart_quote_request_id = 0
        self.chart_quote_after_id: str | None = None
        self.current_frame = pd.DataFrame()
        self.current_frames: dict[str, pd.DataFrame] = {}
        self.current_series_colors: dict[str, str] = {}
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
        self.floating_window_restore_geometries: dict[tk.Widget, dict[str, int]] = {}
        self.layout_save_after_id: str | None = None
        self.layout_manually_saved = False
        self.saved_layout_snapshot: dict = {}
        self.layout_dirty = False
        self.geometry_save_after_id: str | None = None
        source_root = Path(__file__).resolve().parent
        self.source_watch_paths = tuple(source_root / name for name in RUNTIME_SOURCE_FILES)
        self.source_snapshot = source_file_snapshot(self.source_watch_paths)
        self.portfolio_quote_popup: tk.Toplevel | None = None
        self.portfolio_quote_tree: ttk.Treeview | None = None
        self.portfolio_quote_status_var = tk.StringVar(value="")
        self.portfolio_quote_request_id = 0
        self.portfolio_quote_hide_after_id: str | None = None
        self.news_request_id = 0
        self.event_request_id = 0
        self.button_tooltip = ButtonTooltip(self)

        self._configure_styles()
        self._install_button_tooltips()
        self._build_controls()
        self._build_chart()
        self.search_var.trace_add("write", self._on_search_text_changed)
        self.compare_search_var.trace_add("write", self._on_compare_search_text_changed)
        self.watchlist_search_var.trace_add("write", self._on_watchlist_search_text_changed)
        self.event_search_var.trace_add("write", self._on_event_search_text_changed)
        self.bind("<ButtonPress-1>", self._dismiss_suggestions_on_click, add="+")
        self.bind_all("<Control-f>", self._focus_primary_search)
        self.bind_all("<Control-F>", self._focus_primary_search)
        self.bind_all("<ButtonPress-1>", self._start_text_rectangle, add="+")
        self.bind_all("<B1-Motion>", self._drag_text_rectangle, add="+")
        self.bind_all("<ButtonRelease-1>", self._finish_text_rectangle, add="+")
        self.bind_all("<Escape>", self._cancel_text_selection, add="+")
        self.bind("<Configure>", self._schedule_window_geometry_save, add="+")
        self.protocol("WM_DELETE_WINDOW", self._close_app)
        self._restore_startup_chart_state()
        self.after_idle(self._restore_window_state)
        self._tick_header_clocks()
        self.after(SOURCE_WATCH_INTERVAL_MS, self._poll_for_source_update)

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=TEXT, font=(TERMINAL_FONT_FAMILY, 10))
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Status.TLabel", background=BG, foreground=MUTED)
        style.configure(
            "Clock.TLabel",
            background=BG,
            foreground=TEXT,
            font=(TERMINAL_FONT_FAMILY, 10, "bold"),
        )
        style.configure(
            "Update.TLabel",
            background=ORANGE,
            foreground=BG,
            font=(TERMINAL_FONT_FAMILY, 10, "bold"),
        )
        style.configure(
            "Title.TLabel", background=BG, foreground=ORANGE, font=(TERMINAL_FONT_FAMILY, 17, "bold")
        )
        style.configure(
            "Quote.TLabel", background=BG, foreground=TEXT, font=(TERMINAL_FONT_FAMILY, 12, "bold")
        )
        style.configure(
            "ChartOverlay.TLabel",
            background=BG,
            foreground=TEXT,
            font=(TERMINAL_FONT_FAMILY, 9),
        )
        style.configure(
            "ChartOverlayQuote.TLabel",
            background=BG,
            foreground=TEXT,
            font=(TERMINAL_FONT_FAMILY, 11, "bold"),
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
            font=(TERMINAL_FONT_FAMILY, 10, "bold"),
        )
        style.map("Accent.TButton", background=[("active", "#ffc247")])
        style.configure(
            "Chip.TButton",
            background=PANEL,
            foreground=MUTED,
            bordercolor=GRID,
            padding=(9, 5),
            font=(TERMINAL_FONT_FAMILY, 9, "bold"),
        )
        style.map("Chip.TButton", background=[("active", GRID)], foreground=[("active", TEXT)])
        style.configure(
            "Header.TButton",
            background=GRID,
            foreground=MUTED,
            bordercolor=GRID,
            padding=(4, 1),
            font=TITLEBAR_BUTTON_FONT,
        )
        style.map("Header.TButton", background=[("active", PANEL)], foreground=[("active", TEXT)])
        style.configure(
            "Selected.Header.TButton",
            background=ORANGE,
            foreground=BG,
            bordercolor=ORANGE,
            padding=(4, 1),
            font=TITLEBAR_BUTTON_FONT,
        )
        style.map("Selected.Header.TButton", background=[("active", "#ffc247")])
        style.configure(
            "Selected.Chip.TButton",
            background=ORANGE,
            foreground=BG,
            bordercolor=ORANGE,
            padding=(9, 5),
            font=(TERMINAL_FONT_FAMILY, 9, "bold"),
        )
        style.map("Selected.Chip.TButton", background=[("active", "#ffc247")])
        style.configure(
            "Flyout.TButton",
            background=PANEL,
            foreground=TEXT,
            bordercolor=GRID,
            padding=(7, 5),
            font=(TERMINAL_FONT_FAMILY, 9),
        )
        style.map("Flyout.TButton", background=[("active", GRID)])
        style.configure(
            "Selected.Flyout.TButton",
            background=ORANGE,
            foreground=BG,
            bordercolor=ORANGE,
            padding=(7, 5),
            font=(TERMINAL_FONT_FAMILY, 9, "bold"),
        )
        style.configure(
            "Treeview",
            background=PANEL,
            fieldbackground=PANEL,
            foreground=TEXT,
            rowheight=27,
            font=(TERMINAL_FONT_FAMILY, 9),
        )
        style.configure(
            "Treeview.Heading",
            background=GRID,
            foreground=TEXT,
            font=(TERMINAL_FONT_FAMILY, 9, "bold"),
        )
        style.configure(
            "Watchlist.Treeview",
            background=WATCHLIST_ROW_ODD,
            fieldbackground=WATCHLIST_ROW_ODD,
            foreground=TEXT,
            bordercolor=GRID,
            borderwidth=1,
            relief=tk.SOLID,
            rowheight=27,
            font=(TERMINAL_FONT_FAMILY, 9),
        )
        style.configure(
            "Watchlist.Treeview.Heading",
            background=GRID,
            foreground=TEXT,
            bordercolor=BG,
            borderwidth=1,
            relief=tk.RAISED,
            font=(TERMINAL_FONT_FAMILY, 9, "bold"),
        )
        style.map("Treeview", background=[("selected", ORANGE)], foreground=[("selected", BG)])
        style.configure("TCheckbutton", background=BG, foreground=MUTED)
        style.configure(
            "Header.TCheckbutton",
            background=GRID,
            foreground=MUTED,
            indicatorcolor=GRID,
            padding=(4, 1),
            font=TITLEBAR_BUTTON_FONT,
        )
        style.map(
            "Header.TCheckbutton",
            background=[("active", PANEL)],
            foreground=[("selected", TEXT), ("active", TEXT)],
            indicatorcolor=[("selected", ORANGE)],
        )
        style.configure(
            "Chip.TCheckbutton",
            background=PANEL,
            foreground=MUTED,
            indicatorcolor=PANEL,
            padding=(8, 5),
            font=(TERMINAL_FONT_FAMILY, 9, "bold"),
        )
        style.map(
            "Chip.TCheckbutton",
            background=[("active", GRID)],
            foreground=[("selected", TEXT), ("active", TEXT)],
            indicatorcolor=[("selected", ORANGE)],
        )

    def _install_button_tooltips(self) -> None:
        for widget_class in ("Button", "TButton", "TCheckbutton", "Menubutton"):
            self.bind_class(
                widget_class,
                "<Enter>",
                lambda event: self.button_tooltip.schedule(event.widget),
                add="+",
            )
            self.bind_class(
                widget_class,
                "<Leave>",
                lambda _event: self.button_tooltip.cancel(),
                add="+",
            )
            self.bind_class(
                widget_class,
                "<ButtonPress>",
                lambda _event: self.button_tooltip.cancel(),
                add="+",
            )

    def _set_tooltip(self, widget: tk.Widget, text: str) -> tk.Widget:
        widget.tooltip_text = text
        return widget

    def _button_tooltip_text(self, widget: tk.Widget) -> str:
        text = getattr(widget, "tooltip_text", "")
        if text:
            return text
        try:
            label = str(widget.cget("text")).strip()
        except tk.TclError:
            label = ""
        if not label:
            return ""
        normalized = " ".join(label.split()).upper()
        defaults = {
            MAXIMIZE_ICON: "Maximize or restore this panel.",
            "R": "Refresh this panel's data.",
            "T": "Open technical indicator choices.",
            "E": "Toggle extended-hours price data when available.",
            "C": "Open the comparison series panel.",
            "BAR": "Chart type: OHLC bars. Click to cycle chart type.",
            "CND": "Chart type: candles. Click to cycle chart type.",
            "HOL": "Chart type: hollow candles. Click to cycle chart type.",
            "HLC": "Chart type: HLC bars. Click to cycle chart type.",
            "LIN": "Chart type: line. Click to cycle chart type.",
            "MRK": "Chart type: line with markers. Click to cycle chart type.",
            "STP": "Chart type: step line. Click to cycle chart type.",
            "SEC": "Open SEC filing and fundamentals details for the selected ticker.",
            "OPEN": "Open the selected search result in the chart.",
            "ADD TO COMPARE": "Add the selected search result as a comparison series.",
            "REMOVE": "Remove the selected comparison series.",
            "CLEAR": "Clear all comparison series.",
            "APPLY": "Apply the custom intraday range.",
            "APPLY DAILY": "Apply the custom historical daily range.",
            "CLEAR SEC CACHE": "Clear cached SEC data and fetch fresh data next time.",
            "OPEN SELECTED FILING": "Open the selected SEC filing in your browser.",
            "RELOAD APP": "Restart the app to load changed source files.",
            "LATER": "Dismiss this update notice for now.",
        }
        if normalized in defaults:
            return defaults[normalized]
        if normalized.startswith("MOM "):
            return f"Show {normalized.lower()} price momentum."
        if normalized.startswith("SIG "):
            return f"Show {normalized.lower()} volatility."
        if normalized.startswith("RSI "):
            return f"Show {normalized} relative strength index."
        if normalized == "VOLUME":
            return "Show volume below the price chart."
        if re.fullmatch(r"\d+[MDY]", normalized) or normalized == "YTD":
            return f"Switch the chart to the {label} historical range."
        if re.fullmatch(r"\d+M", normalized):
            return f"Use {label} intraday bars for the selected window."
        if normalized in set("ABCDEF"):
            return f"Link this panel to group {normalized}."
        return f"Run {label}."

    def _tick_header_clocks(self) -> None:
        local_now = datetime.now().astimezone()
        self.local_time_var.set(f"LOCAL {local_now:%H:%M:%S}")
        self.new_york_time_var.set(
            f"NEW YORK {datetime.now(ZoneInfo('America/New_York')):%H:%M:%S}"
        )
        self.london_time_var.set(f"LONDON {datetime.now(ZoneInfo('Europe/London')):%H:%M:%S}")
        self.hong_kong_time_var.set(
            f"HK {datetime.now(ZoneInfo('Asia/Hong_Kong')):%H:%M:%S}"
        )
        self.after(1000, self._tick_header_clocks)

    def _build_controls(self) -> None:
        header = ttk.Frame(self, padding=(18, 13, 18, 8))
        header.pack(fill=tk.X)
        for variable in reversed(
            (
                self.local_time_var,
                self.new_york_time_var,
                self.london_time_var,
                self.hong_kong_time_var,
            )
        ):
            ttk.Label(header, textvariable=variable, style="Clock.TLabel").pack(
                side=tk.RIGHT, padx=(18, 0)
            )

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
        self.chart_titlebar = tk.Frame(self.chart_window, bg=GRID, height=TITLEBAR_HEIGHT, cursor="fleur")
        self.chart_titlebar.pack(fill=tk.X)
        self.chart_titlebar.pack_propagate(False)
        title_label = self._build_titlebar_label(self.chart_titlebar, "CHART")
        title_label.bind("<ButtonPress-1>", self._start_chart_window_drag)
        title_label.bind("<B1-Motion>", self._drag_chart_window)
        title_label.bind("<ButtonRelease-1>", self._finish_chart_window_drag)
        self.chart_header_summary_label = tk.Label(
            self.chart_titlebar,
            textvariable=self.chart_header_summary_var,
            bg=GRID,
            fg=TEXT,
            font=TITLEBAR_BUTTON_FONT,
            anchor=tk.W,
            padx=6,
        )
        self.chart_header_summary_label.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=3)
        self.chart_header_summary_label.bind("<Enter>", self._show_portfolio_quote_popup)
        self.chart_header_summary_label.bind("<Leave>", self._hide_portfolio_quote_popup)
        self.search_entry = tk.Entry(
            self.chart_titlebar,
            textvariable=self.search_var,
            bg=BG,
            fg=TEXT,
            insertbackground=TEXT,
            relief=tk.FLAT,
            font=(TERMINAL_FONT_FAMILY, 10),
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
        self._build_titlebar_button(self.chart_titlebar, MAXIMIZE_ICON, self._maximize_chart_window)
        self.chart_titlebar.bind("<ButtonPress-1>", self._start_chart_window_drag)
        self.chart_titlebar.bind("<B1-Motion>", self._drag_chart_window)
        self.chart_titlebar.bind("<ButtonRelease-1>", self._finish_chart_window_drag)
        self.chart_panel = ttk.Frame(self.chart_window, style="Panel.TFrame", padding=(8, 7, 8, 0))
        self.chart_panel.pack(fill=tk.BOTH, expand=True)
        self._build_chart_toolbar()
        self.chart_resize_grip = tk.Frame(
            self.chart_window,
            bg=PANEL,
            width=15,
            height=15,
            cursor="size_nw_se",
        )
        self.chart_resize_grip.place(relx=1.0, rely=1.0, anchor=tk.SE)
        self.chart_resize_grip.bind("<ButtonPress-1>", self._start_chart_window_resize)
        self.chart_resize_grip.bind("<B1-Motion>", self._resize_chart_window)
        self.chart_resize_grip.bind("<ButtonRelease-1>", self._finish_chart_window_resize)
        self.identity_label = ttk.Label(
            self.chart_panel, textvariable=self.identity_var, style="ChartOverlay.TLabel"
        )
        self.identity_label.pack(
            anchor=tk.W, pady=(0, 3)
        )
        self.quote_label = ttk.Label(
            self.chart_panel, textvariable=self.quote_var, style="ChartOverlayQuote.TLabel", cursor="hand2"
        )
        self.quote_label.pack(anchor=tk.W, pady=(0, 6))
        self.quote_label.bind("<Enter>", self._show_portfolio_quote_popup)
        self.quote_label.bind("<Leave>", self._hide_portfolio_quote_popup)
        self.fundamentals_label = ttk.Label(
            self.chart_panel, textvariable=self.fundamentals_var, style="ChartOverlay.TLabel"
        )
        self.fundamentals_label.pack(
            anchor=tk.W, pady=(0, 4)
        )
        self.sec_context_label = ttk.Label(
            self.chart_panel,
            textvariable=self.sec_context_var,
            style="ChartOverlay.TLabel",
            wraplength=1200,
        )
        self.sec_context_label.pack(anchor=tk.W, pady=(0, 4))
        self.measurement_label = ttk.Label(
            self.chart_panel, textvariable=self.measurement_var, style="ChartOverlay.TLabel"
        )
        self.measurement_label.pack(anchor=tk.W, pady=(0, 4))
        self._hide_chart_metadata_labels()
        self._build_suggestion_popup()
        self._build_watchlist_window()
        self._build_macro_window()
        self._build_news_window()
        self._build_event_window()
        self.after_idle(self._layout_initial_workspace_windows)
        self.after_idle(self.refresh_watchlist)
        self.after_idle(self._schedule_watchlist_refresh_loop)

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
        self.event_window.place_configure(
            x=0,
            y=min(height, 520) + 10,
            width=watch_width,
            height=max(MIN_EVENT_WINDOW_HEIGHT, min(320, height - min(height, 520) - 10)),
        )
        self._hide_macro_window()
        self._hide_news_window()
        self._mark_layout_saved_snapshot()

    def _restore_saved_function_layout(self) -> bool:
        if not self.saved_layout_state:
            return False
        restored = False
        for name, widget, minimum_width, minimum_height in (
            ("watchlist", self.watchlist_window, 360, 260),
            ("chart", self.chart_window, MIN_CHART_WINDOW_WIDTH, MIN_CHART_WINDOW_HEIGHT),
            ("events", self.event_window, MIN_EVENT_WINDOW_WIDTH, MIN_EVENT_WINDOW_HEIGHT),
            ("macro", self.macro_window, MIN_MACRO_WINDOW_WIDTH, MIN_MACRO_WINDOW_HEIGHT),
            ("news", self.news_window, MIN_NEWS_WINDOW_WIDTH, MIN_NEWS_WINDOW_HEIGHT),
        ):
            geometry = self.saved_layout_state.get(name)
            if not isinstance(geometry, dict):
                continue
            visible = bool(geometry.get("visible", name in {"watchlist", "chart", "events"}))
            if not visible and name not in {"watchlist", "chart", "events"}:
                widget.place_forget()
                restored = True
                continue
            x = int(geometry.get("x", 0))
            y = int(geometry.get("y", 0))
            width = max(int(geometry.get("width", minimum_width)), minimum_width)
            height = max(int(geometry.get("height", minimum_height)), minimum_height)
            widget.place_configure(x=x, y=y, width=width, height=height)
            restored = True
        self._constrain_chart_window_to_desktop(None)
        self._constrain_watchlist_window_to_desktop()
        if self.macro_window.winfo_manager() == "place":
            self._constrain_macro_window_to_desktop()
        if self.news_window.winfo_manager() == "place":
            self._constrain_news_window_to_desktop()
        if self.event_window.winfo_manager() == "place":
            self._constrain_event_window_to_desktop()
        elif "events" not in self.saved_layout_state and SHOW_EVENT_WINDOW:
            self.event_window.place(x=0, y=520, width=MIN_EVENT_WINDOW_WIDTH, height=MIN_EVENT_WINDOW_HEIGHT)
        self.after(250, self._apply_saved_function_layout_without_constraints)
        return restored

    def _apply_saved_function_layout_without_constraints(self) -> None:
        for name, widget in (
            ("watchlist", self.watchlist_window),
            ("chart", self.chart_window),
            ("events", self.event_window),
            ("macro", self.macro_window),
            ("news", self.news_window),
        ):
            geometry = self.saved_layout_state.get(name)
            if not isinstance(geometry, dict):
                continue
            visible = bool(geometry.get("visible", name in {"watchlist", "chart", "events"}))
            if not visible and name not in {"watchlist", "chart", "events"}:
                widget.place_forget()
                continue
            widget.place_configure(
                x=int(geometry.get("x", widget.winfo_x())),
                y=int(geometry.get("y", widget.winfo_y())),
                width=int(geometry.get("width", widget.winfo_width())),
                height=int(geometry.get("height", widget.winfo_height())),
            )

    def _hide_macro_window(self) -> None:
        if hasattr(self, "macro_window") and not SHOW_MACRO_WINDOW:
            self.macro_window.place_forget()

    def _hide_news_window(self) -> None:
        if hasattr(self, "news_window") and not SHOW_NEWS_WINDOW:
            self.news_window.place_forget()

    def _build_titlebar_label(self, parent: tk.Widget, text: str) -> tk.Label:
        label = tk.Label(
            parent,
            text=text,
            bg=GRID,
            fg=TEXT,
            font=TITLEBAR_TITLE_FONT,
            padx=8,
        )
        label.pack(side=tk.LEFT)
        return label

    def _build_titlebar_button(
        self, parent: tk.Widget, text: str, command, width: int = TITLEBAR_BUTTON_WIDTH
    ) -> tk.Button:
        button = tk.Button(
            parent,
            text=text,
            command=command,
            width=width,
            bg=GRID,
            fg=MUTED,
            activebackground=PANEL,
            activeforeground=TEXT,
            relief=tk.FLAT,
            font=TITLEBAR_BUTTON_FONT,
            padx=TITLEBAR_BUTTON_PADX,
            pady=TITLEBAR_BUTTON_PADY,
        )
        button.pack(side=tk.RIGHT, padx=(0, 3), pady=3)
        return button

    def _configure_titlebar_menu(self, menu: tk.OptionMenu, width: int = TITLEBAR_MENU_WIDTH) -> None:
        menu.configure(
            bg=GRID,
            fg=MUTED,
            activebackground=PANEL,
            activeforeground=TEXT,
            relief=tk.FLAT,
            font=TITLEBAR_BUTTON_FONT,
            padx=2,
            pady=0,
            width=width,
            highlightthickness=0,
        )
        menu["menu"].configure(bg=PANEL, fg=TEXT, activebackground=ORANGE, activeforeground=BG)

    def _build_group_selector(
        self,
        parent: tk.Widget,
        variable: tk.StringVar,
        command,
        side: str = tk.LEFT,
        padx: tuple[int, int] = (2, 4),
    ) -> tk.OptionMenu:
        menu = tk.OptionMenu(parent, variable, *tuple("ABCDEF"), command=command)
        menu.configure(
            bg=ORANGE,
            fg=BG,
            activebackground="#ffc247",
            activeforeground=BG,
            relief=tk.FLAT,
            font=TITLEBAR_BUTTON_FONT,
            padx=2,
            pady=0,
            width=1,
            highlightthickness=0,
        )
        menu["menu"].configure(bg=PANEL, fg=TEXT, activebackground=ORANGE, activeforeground=BG)
        menu.pack(side=side, padx=padx, pady=3)
        return menu

    def _on_chart_group_changed(self, _value: str | None = None) -> None:
        self.status_var.set(f"Chart linked to group {self.chart_group_var.get()}.")
        self._schedule_function_layout_save()

    def _on_watchlist_group_changed(self, _value: str | None = None) -> None:
        self.status_var.set(f"Watchlist linked to group {self.watchlist_group_var.get()}.")
        self._schedule_function_layout_save()

    def _on_event_group_changed(self, _value: str | None = None) -> None:
        self.status_var.set(f"Event calendar linked to group {self.event_group_var.get()}.")
        self._schedule_function_layout_save()
        selected = self.watchlist_tree.selection() if hasattr(self, "watchlist_tree") else ()
        if selected:
            instrument = self.watchlist_instruments.get(selected[0])
            if instrument is not None:
                self._schedule_grouped_events_from_watchlist(instrument)

    def _maximize_chart_window(self) -> None:
        self._maximize_floating_window(self.chart_window, MIN_CHART_WINDOW_WIDTH, MIN_CHART_WINDOW_HEIGHT)

    def _maximize_watchlist_window(self) -> None:
        self._maximize_floating_window(self.watchlist_window, 360, 260)

    def _maximize_macro_window(self) -> None:
        self._maximize_floating_window(self.macro_window, MIN_MACRO_WINDOW_WIDTH, MIN_MACRO_WINDOW_HEIGHT)

    def _maximize_news_window(self) -> None:
        self._maximize_floating_window(self.news_window, MIN_NEWS_WINDOW_WIDTH, MIN_NEWS_WINDOW_HEIGHT)

    def _maximize_event_window(self) -> None:
        self._maximize_floating_window(self.event_window, MIN_EVENT_WINDOW_WIDTH, MIN_EVENT_WINDOW_HEIGHT)

    def _maximize_floating_window(
        self, window: tk.Widget, minimum_width: int, minimum_height: int
    ) -> None:
        self.desktop.update_idletasks()
        restore_geometry = self.floating_window_restore_geometries.pop(window, None)
        if restore_geometry is not None:
            window.lift()
            window.place_configure(**restore_geometry)
            self._mark_layout_dirty_if_changed()
            return
        self.floating_window_restore_geometries[window] = {
            "x": window.winfo_x(),
            "y": window.winfo_y(),
            "width": window.winfo_width(),
            "height": window.winfo_height(),
        }
        width = max(self.desktop.winfo_width(), minimum_width)
        height = max(self.desktop.winfo_height(), minimum_height)
        window.lift()
        window.place_configure(x=0, y=0, width=width, height=height)
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
        if hasattr(self, "macro_window") and SHOW_MACRO_WINDOW:
            self._constrain_macro_window_to_desktop()
        if hasattr(self, "news_window") and SHOW_NEWS_WINDOW:
            self._constrain_news_window_to_desktop()
        if hasattr(self, "event_window"):
            self._constrain_event_window_to_desktop()

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

    def _constrain_macro_window_to_desktop(self) -> None:
        if self.desktop.winfo_width() < MIN_MACRO_WINDOW_WIDTH or self.desktop.winfo_height() < MIN_MACRO_WINDOW_HEIGHT:
            return
        desktop_width = max(self.desktop.winfo_width(), MIN_MACRO_WINDOW_WIDTH)
        desktop_height = max(self.desktop.winfo_height(), MIN_MACRO_WINDOW_HEIGHT)
        left = max(0, min(self.macro_window.winfo_x(), max(desktop_width - MIN_MACRO_WINDOW_WIDTH, 0)))
        top = max(0, min(self.macro_window.winfo_y(), max(desktop_height - MIN_MACRO_WINDOW_HEIGHT, 0)))
        width = min(max(self.macro_window.winfo_width(), MIN_MACRO_WINDOW_WIDTH), desktop_width - left)
        height = min(max(self.macro_window.winfo_height(), MIN_MACRO_WINDOW_HEIGHT), desktop_height - top)
        self.macro_window.place_configure(x=left, y=top, width=width, height=height)

    def _constrain_news_window_to_desktop(self) -> None:
        if self.desktop.winfo_width() < MIN_NEWS_WINDOW_WIDTH or self.desktop.winfo_height() < MIN_NEWS_WINDOW_HEIGHT:
            return
        desktop_width = max(self.desktop.winfo_width(), MIN_NEWS_WINDOW_WIDTH)
        desktop_height = max(self.desktop.winfo_height(), MIN_NEWS_WINDOW_HEIGHT)
        left = max(0, min(self.news_window.winfo_x(), max(desktop_width - MIN_NEWS_WINDOW_WIDTH, 0)))
        top = max(0, min(self.news_window.winfo_y(), max(desktop_height - MIN_NEWS_WINDOW_HEIGHT, 0)))
        width = min(max(self.news_window.winfo_width(), MIN_NEWS_WINDOW_WIDTH), desktop_width - left)
        height = min(max(self.news_window.winfo_height(), MIN_NEWS_WINDOW_HEIGHT), desktop_height - top)
        self.news_window.place_configure(x=left, y=top, width=width, height=height)

    def _constrain_event_window_to_desktop(self) -> None:
        if self.desktop.winfo_width() < MIN_EVENT_WINDOW_WIDTH or self.desktop.winfo_height() < MIN_EVENT_WINDOW_HEIGHT:
            return
        desktop_width = max(self.desktop.winfo_width(), MIN_EVENT_WINDOW_WIDTH)
        desktop_height = max(self.desktop.winfo_height(), MIN_EVENT_WINDOW_HEIGHT)
        left = max(0, min(self.event_window.winfo_x(), max(desktop_width - MIN_EVENT_WINDOW_WIDTH, 0)))
        top = max(0, min(self.event_window.winfo_y(), max(desktop_height - MIN_EVENT_WINDOW_HEIGHT, 0)))
        width = min(max(self.event_window.winfo_width(), MIN_EVENT_WINDOW_WIDTH), desktop_width - left)
        height = min(max(self.event_window.winfo_height(), MIN_EVENT_WINDOW_HEIGHT), desktop_height - top)
        self.event_window.place_configure(x=left, y=top, width=width, height=height)

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

    def _start_macro_window_drag(self, event: tk.Event) -> str:
        self.macro_window.lift()
        self.floating_window_drag = {
            "x": event.x_root,
            "y": event.y_root,
            "left": self.macro_window.winfo_x(),
            "top": self.macro_window.winfo_y(),
        }
        return "break"

    def _drag_macro_window(self, event: tk.Event) -> str:
        if not self.floating_window_drag:
            return "break"
        desktop_width = max(self.desktop.winfo_width(), MIN_MACRO_WINDOW_WIDTH)
        desktop_height = max(self.desktop.winfo_height(), MIN_MACRO_WINDOW_HEIGHT)
        width = self.macro_window.winfo_width()
        height = self.macro_window.winfo_height()
        left = self.floating_window_drag["left"] + event.x_root - self.floating_window_drag["x"]
        top = self.floating_window_drag["top"] + event.y_root - self.floating_window_drag["y"]
        left = max(0, min(left, max(desktop_width - width, 0)))
        top = max(0, min(top, max(desktop_height - height, 0)))
        self.macro_window.place_configure(x=left, y=top)
        self._mark_layout_dirty_if_changed()
        return "break"

    def _finish_macro_window_drag(self, _event: tk.Event) -> str:
        self.floating_window_drag = None
        self._mark_layout_dirty_if_changed()
        return "break"

    def _start_macro_window_resize(self, event: tk.Event) -> str:
        self.macro_window.lift()
        self.floating_window_resize = {
            "x": event.x_root,
            "y": event.y_root,
            "width": self.macro_window.winfo_width(),
            "height": self.macro_window.winfo_height(),
        }
        return "break"

    def _resize_macro_window(self, event: tk.Event) -> str:
        if not self.floating_window_resize:
            return "break"
        left = self.macro_window.winfo_x()
        top = self.macro_window.winfo_y()
        desktop_width = max(self.desktop.winfo_width(), MIN_MACRO_WINDOW_WIDTH)
        desktop_height = max(self.desktop.winfo_height(), MIN_MACRO_WINDOW_HEIGHT)
        width = self.floating_window_resize["width"] + event.x_root - self.floating_window_resize["x"]
        height = self.floating_window_resize["height"] + event.y_root - self.floating_window_resize["y"]
        width = max(MIN_MACRO_WINDOW_WIDTH, min(width, max(desktop_width - left, MIN_MACRO_WINDOW_WIDTH)))
        height = max(MIN_MACRO_WINDOW_HEIGHT, min(height, max(desktop_height - top, MIN_MACRO_WINDOW_HEIGHT)))
        self.macro_window.place_configure(width=width, height=height)
        self._mark_layout_dirty_if_changed()
        return "break"

    def _finish_macro_window_resize(self, _event: tk.Event) -> str:
        self.floating_window_resize = None
        self._mark_layout_dirty_if_changed()
        return "break"

    def _start_news_window_drag(self, event: tk.Event) -> str:
        self.news_window.lift()
        self.floating_window_drag = {
            "x": event.x_root,
            "y": event.y_root,
            "left": self.news_window.winfo_x(),
            "top": self.news_window.winfo_y(),
        }
        return "break"

    def _drag_news_window(self, event: tk.Event) -> str:
        if not self.floating_window_drag:
            return "break"
        desktop_width = max(self.desktop.winfo_width(), MIN_NEWS_WINDOW_WIDTH)
        desktop_height = max(self.desktop.winfo_height(), MIN_NEWS_WINDOW_HEIGHT)
        width = self.news_window.winfo_width()
        height = self.news_window.winfo_height()
        left = self.floating_window_drag["left"] + event.x_root - self.floating_window_drag["x"]
        top = self.floating_window_drag["top"] + event.y_root - self.floating_window_drag["y"]
        left = max(0, min(left, max(desktop_width - width, 0)))
        top = max(0, min(top, max(desktop_height - height, 0)))
        self.news_window.place_configure(x=left, y=top)
        self._mark_layout_dirty_if_changed()
        return "break"

    def _finish_news_window_drag(self, _event: tk.Event) -> str:
        self.floating_window_drag = None
        self._mark_layout_dirty_if_changed()
        return "break"

    def _start_news_window_resize(self, event: tk.Event) -> str:
        self.news_window.lift()
        self.floating_window_resize = {
            "x": event.x_root,
            "y": event.y_root,
            "width": self.news_window.winfo_width(),
            "height": self.news_window.winfo_height(),
        }
        return "break"

    def _resize_news_window(self, event: tk.Event) -> str:
        if not self.floating_window_resize:
            return "break"
        left = self.news_window.winfo_x()
        top = self.news_window.winfo_y()
        desktop_width = max(self.desktop.winfo_width(), MIN_NEWS_WINDOW_WIDTH)
        desktop_height = max(self.desktop.winfo_height(), MIN_NEWS_WINDOW_HEIGHT)
        width = self.floating_window_resize["width"] + event.x_root - self.floating_window_resize["x"]
        height = self.floating_window_resize["height"] + event.y_root - self.floating_window_resize["y"]
        width = max(MIN_NEWS_WINDOW_WIDTH, min(width, max(desktop_width - left, MIN_NEWS_WINDOW_WIDTH)))
        height = max(MIN_NEWS_WINDOW_HEIGHT, min(height, max(desktop_height - top, MIN_NEWS_WINDOW_HEIGHT)))
        self.news_window.place_configure(width=width, height=height)
        self._mark_layout_dirty_if_changed()
        return "break"

    def _finish_news_window_resize(self, _event: tk.Event) -> str:
        self.floating_window_resize = None
        self._mark_layout_dirty_if_changed()
        return "break"

    def _start_event_window_drag(self, event: tk.Event) -> str:
        self.event_window.lift()
        self.floating_window_drag = {
            "x": event.x_root,
            "y": event.y_root,
            "left": self.event_window.winfo_x(),
            "top": self.event_window.winfo_y(),
        }
        return "break"

    def _drag_event_window(self, event: tk.Event) -> str:
        if not self.floating_window_drag:
            return "break"
        desktop_width = max(self.desktop.winfo_width(), MIN_EVENT_WINDOW_WIDTH)
        desktop_height = max(self.desktop.winfo_height(), MIN_EVENT_WINDOW_HEIGHT)
        width = self.event_window.winfo_width()
        height = self.event_window.winfo_height()
        left = self.floating_window_drag["left"] + event.x_root - self.floating_window_drag["x"]
        top = self.floating_window_drag["top"] + event.y_root - self.floating_window_drag["y"]
        left = max(0, min(left, max(desktop_width - width, 0)))
        top = max(0, min(top, max(desktop_height - height, 0)))
        self.event_window.place_configure(x=left, y=top)
        self._mark_layout_dirty_if_changed()
        return "break"

    def _finish_event_window_drag(self, _event: tk.Event) -> str:
        self.floating_window_drag = None
        self._mark_layout_dirty_if_changed()
        return "break"

    def _start_event_window_resize(self, event: tk.Event) -> str:
        self.event_window.lift()
        self.floating_window_resize = {
            "x": event.x_root,
            "y": event.y_root,
            "width": self.event_window.winfo_width(),
            "height": self.event_window.winfo_height(),
        }
        return "break"

    def _resize_event_window(self, event: tk.Event) -> str:
        if not self.floating_window_resize:
            return "break"
        left = self.event_window.winfo_x()
        top = self.event_window.winfo_y()
        desktop_width = max(self.desktop.winfo_width(), MIN_EVENT_WINDOW_WIDTH)
        desktop_height = max(self.desktop.winfo_height(), MIN_EVENT_WINDOW_HEIGHT)
        width = self.floating_window_resize["width"] + event.x_root - self.floating_window_resize["x"]
        height = self.floating_window_resize["height"] + event.y_root - self.floating_window_resize["y"]
        width = max(MIN_EVENT_WINDOW_WIDTH, min(width, max(desktop_width - left, MIN_EVENT_WINDOW_WIDTH)))
        height = max(MIN_EVENT_WINDOW_HEIGHT, min(height, max(desktop_height - top, MIN_EVENT_WINDOW_HEIGHT)))
        self.event_window.place_configure(width=width, height=height)
        self._mark_layout_dirty_if_changed()
        return "break"

    def _finish_event_window_resize(self, _event: tk.Event) -> str:
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
        self.watchlist_titlebar = tk.Frame(self.watchlist_window, bg=GRID, height=TITLEBAR_HEIGHT, cursor="fleur")
        self.watchlist_titlebar.pack(fill=tk.X)
        self.watchlist_titlebar.pack_propagate(False)
        label = self._build_titlebar_label(self.watchlist_titlebar, "WATCHLIST")
        for widget in (self.watchlist_titlebar, label):
            widget.bind("<ButtonPress-1>", self._start_watchlist_window_drag)
            widget.bind("<B1-Motion>", self._drag_watchlist_window)
            widget.bind("<ButtonRelease-1>", self._finish_watchlist_window_drag)
        self._build_titlebar_button(
            self.watchlist_titlebar, MAXIMIZE_ICON, self._maximize_watchlist_window
        )
        self._build_group_selector(
            self.watchlist_titlebar,
            self.watchlist_group_var,
            self._on_watchlist_group_changed,
            side=tk.RIGHT,
            padx=(0, 3),
        )
        self._build_titlebar_button(self.watchlist_titlebar, "R", self.refresh_watchlist)
        content = ttk.Frame(self.watchlist_window, style="Panel.TFrame", padding=7)
        content.pack(fill=tk.BOTH, expand=True)
        tree_frame = ttk.Frame(content, style="Panel.TFrame")
        tree_frame.pack(fill=tk.BOTH, expand=True)
        self.watchlist_tree = ttk.Treeview(
            tree_frame,
            columns=("asset", "last", "bid", "ask", "change", "volume", "latency"),
            show="headings",
            height=12,
            style="Watchlist.Treeview",
        )
        self.watchlist_scrollbar = ttk.Scrollbar(
            tree_frame,
            orient=tk.VERTICAL,
            command=self.watchlist_tree.yview,
        )
        self.watchlist_tree.configure(yscrollcommand=self.watchlist_scrollbar.set)
        self.watchlist_tree.tag_configure("watchlist_even", background=WATCHLIST_ROW_EVEN)
        self.watchlist_tree.tag_configure("watchlist_odd", background=WATCHLIST_ROW_ODD)
        self.watchlist_tree.tag_configure("tick_up", foreground=UP)
        self.watchlist_tree.tag_configure("tick_down", foreground=DOWN)
        self.watchlist_tree.tag_configure("tick_flat", foreground=TEXT)
        self.watchlist_tree.tag_configure("tick_error", foreground=DOWN)
        self.watchlist_tree.tag_configure(
            "watchlist_group",
            background=GRID,
            foreground=ORANGE,
            font=(TERMINAL_FONT_FAMILY, 9, "bold"),
        )
        saved_widths = normalized_watchlist_column_widths(
            self.saved_layout_state.get("watchlist_columns")
        )
        for column, title, default_width in WATCHLIST_COLUMNS:
            anchor = tk.W if column == "asset" else tk.E
            self.watchlist_tree.heading(column, text=title, anchor=tk.CENTER)
            self.watchlist_tree.column(
                column,
                width=saved_widths.get(column, default_width),
                minwidth=WATCHLIST_MIN_COLUMN_WIDTH,
                anchor=anchor,
            )
        self.watchlist_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.watchlist_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.watchlist_tree.bind("<ButtonPress-1>", self._start_watchlist_row_drag, add="+")
        self.watchlist_tree.bind("<B1-Motion>", self._drag_watchlist_row, add="+")
        self.watchlist_tree.bind("<ButtonRelease-1>", self._finish_watchlist_row_drag, add="+")
        self.watchlist_tree.bind("<Double-Button-1>", self._begin_watchlist_asset_search)
        self.watchlist_tree.bind("<Button-3>", self._show_watchlist_context_menu)
        self.watchlist_tree.bind("<<TreeviewSelect>>", self._on_watchlist_selection_changed)
        self.watchlist_column_separators = [
            tk.Frame(tree_frame, bg=GRID, width=1, cursor="")
            for _column in self.watchlist_tree["columns"][:-1]
        ]
        self.watchlist_tree.bind("<Configure>", self._position_watchlist_column_separators, add="+")
        self.watchlist_tree.bind("<B1-Motion>", self._position_watchlist_column_separators, add="+")
        self.watchlist_tree.bind("<ButtonRelease-1>", self._finish_watchlist_column_resize, add="+")
        self.after_idle(self._position_watchlist_column_separators)
        ttk.Label(
            content,
            text="Double-click Asset to search. Right-click rows to insert or remove.",
            style="Status.TLabel",
        ).pack(anchor=tk.W, pady=(7, 0))
        self.watchlist_context_menu = tk.Menu(
            self.watchlist_tree,
            tearoff=False,
            bg=PANEL,
            fg=TEXT,
            activebackground=ORANGE,
            activeforeground=BG,
            relief=tk.FLAT,
        )
        self.watchlist_resize_grip = tk.Frame(
            self.watchlist_window,
            bg=PANEL,
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
        self._ensure_watchlist_trailing_empty_row()

    def _build_macro_window(self) -> None:
        self.macro_window = tk.Frame(
            self.desktop,
            bg=PANEL,
            highlightbackground=GRID,
            highlightthickness=1,
        )
        if SHOW_MACRO_WINDOW:
            self.macro_window.place(x=0, y=530, width=430, height=340)
        self.macro_titlebar = tk.Frame(self.macro_window, bg=GRID, height=TITLEBAR_HEIGHT, cursor="fleur")
        self.macro_titlebar.pack(fill=tk.X)
        self.macro_titlebar.pack_propagate(False)
        label = self._build_titlebar_label(self.macro_titlebar, "MACRO")
        for widget in (self.macro_titlebar, label):
            widget.bind("<ButtonPress-1>", self._start_macro_window_drag)
            widget.bind("<B1-Motion>", self._drag_macro_window)
            widget.bind("<ButtonRelease-1>", self._finish_macro_window_drag)
        self._build_titlebar_button(self.macro_titlebar, MAXIMIZE_ICON, self._maximize_macro_window)
        category_menu = tk.OptionMenu(
            self.macro_titlebar,
            self.macro_category_var,
            "rates",
            "inflation",
            "labor",
            "growth",
            "money",
            "credit",
            command=self._select_macro_category,
        )
        self._configure_titlebar_menu(category_menu)
        category_menu.pack(side=tk.RIGHT, padx=(0, 3), pady=3)
        self._build_titlebar_button(self.macro_titlebar, "R", self.refresh_macro_dashboard)
        content = ttk.Frame(self.macro_window, style="Panel.TFrame", padding=7)
        content.pack(fill=tk.BOTH, expand=True)
        self.macro_tree = ttk.Treeview(
            content,
            columns=("series", "title", "latest", "change", "date"),
            show="headings",
            height=8,
        )
        for column, title, width in (
            ("series", "Series", 92),
            ("title", "Indicator", 210),
            ("latest", "Latest", 90),
            ("change", "Chg", 75),
            ("date", "Date", 95),
        ):
            self.macro_tree.heading(column, text=title)
            self.macro_tree.column(column, width=width, anchor=tk.W)
        self.macro_tree.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            content,
            textvariable=self.macro_status_var,
            style="Status.TLabel",
            padding=(0, 6),
        ).pack(fill=tk.X)
        self.macro_resize_grip = tk.Frame(
            self.macro_window,
            bg=PANEL,
            width=15,
            height=15,
            cursor="size_nw_se",
        )
        self.macro_resize_grip.place(relx=1.0, rely=1.0, anchor=tk.SE)
        self.macro_resize_grip.bind("<ButtonPress-1>", self._start_macro_window_resize)
        self.macro_resize_grip.bind("<B1-Motion>", self._resize_macro_window)
        self.macro_resize_grip.bind("<ButtonRelease-1>", self._finish_macro_window_resize)
        self._populate_macro_placeholders()

    def _build_news_window(self) -> None:
        self.news_window = tk.Frame(
            self.desktop,
            bg=PANEL,
            highlightbackground=GRID,
            highlightthickness=1,
        )
        if SHOW_NEWS_WINDOW:
            self.news_window.place(x=460, y=520, width=820, height=360)
        self.news_titlebar = tk.Frame(self.news_window, bg=GRID, height=TITLEBAR_HEIGHT, cursor="fleur")
        self.news_titlebar.pack(fill=tk.X)
        self.news_titlebar.pack_propagate(False)
        label = self._build_titlebar_label(self.news_titlebar, "NEWS")
        for widget in (self.news_titlebar, label):
            widget.bind("<ButtonPress-1>", self._start_news_window_drag)
            widget.bind("<B1-Motion>", self._drag_news_window)
            widget.bind("<ButtonRelease-1>", self._finish_news_window_drag)
        self._build_titlebar_button(self.news_titlebar, MAXIMIZE_ICON, self._maximize_news_window)
        topic_menu = tk.OptionMenu(
            self.news_titlebar,
            self.news_topic_var,
            *tuple(query.label for query in default_news_queries()),
            command=self._select_news_topic,
        )
        self._configure_titlebar_menu(topic_menu, width=10)
        topic_menu.pack(side=tk.RIGHT, padx=(0, 3), pady=3)
        self._build_titlebar_button(self.news_titlebar, "R", self.refresh_news_feed)
        content = ttk.Frame(self.news_window, style="Panel.TFrame", padding=7)
        content.pack(fill=tk.BOTH, expand=True)
        self.news_tree = ttk.Treeview(
            content,
            columns=("time", "source", "title", "domain"),
            show="headings",
            height=9,
        )
        for column, title, width in (
            ("time", "Seen", 115),
            ("source", "Source", 95),
            ("title", "Headline", 460),
            ("domain", "Domain", 150),
        ):
            self.news_tree.heading(column, text=title)
            self.news_tree.column(column, width=width, anchor=tk.W)
        self.news_tree.pack(fill=tk.BOTH, expand=True)
        self.news_tree.bind("<Double-Button-1>", self._open_selected_news_article)
        ttk.Label(
            content,
            textvariable=self.news_status_var,
            style="Status.TLabel",
            padding=(0, 6),
        ).pack(fill=tk.X)
        self.news_resize_grip = tk.Frame(
            self.news_window,
            bg=PANEL,
            width=15,
            height=15,
            cursor="size_nw_se",
        )
        self.news_resize_grip.place(relx=1.0, rely=1.0, anchor=tk.SE)
        self.news_resize_grip.bind("<ButtonPress-1>", self._start_news_window_resize)
        self.news_resize_grip.bind("<B1-Motion>", self._resize_news_window)
        self.news_resize_grip.bind("<ButtonRelease-1>", self._finish_news_window_resize)

    def _build_event_window(self) -> None:
        self.event_window = tk.Frame(
            self.desktop,
            bg=PANEL,
            highlightbackground=GRID,
            highlightthickness=1,
        )
        if SHOW_EVENT_WINDOW:
            self.event_window.place(x=0, y=520, width=560, height=300)
        self.event_titlebar = tk.Frame(self.event_window, bg=GRID, height=TITLEBAR_HEIGHT, cursor="fleur")
        self.event_titlebar.pack(fill=tk.X)
        self.event_titlebar.pack_propagate(False)
        label = self._build_titlebar_label(self.event_titlebar, "EVENTS")
        for widget in (self.event_titlebar, label):
            widget.bind("<ButtonPress-1>", self._start_event_window_drag)
            widget.bind("<B1-Motion>", self._drag_event_window)
            widget.bind("<ButtonRelease-1>", self._finish_event_window_drag)
        self._build_titlebar_button(self.event_titlebar, MAXIMIZE_ICON, self._maximize_event_window)
        self._build_group_selector(
            self.event_titlebar,
            self.event_group_var,
            self._on_event_group_changed,
            side=tk.RIGHT,
            padx=(0, 3),
        )
        self._build_titlebar_button(self.event_titlebar, "R", self.refresh_event_calendar)
        self.event_search_entry = tk.Entry(
            self.event_titlebar,
            textvariable=self.event_search_var,
            bg=BG,
            fg=TEXT,
            insertbackground=TEXT,
            relief=tk.FLAT,
            font=(TERMINAL_FONT_FAMILY, 10),
        )
        self.event_search_entry.pack(side=tk.RIGHT, fill=tk.X, expand=True, ipady=4, padx=(6, 8), pady=3)
        self.event_search_entry.bind("<Return>", self._accept_or_search)
        self.event_search_entry.bind("<Control-a>", self._select_all_event_search_text)
        self.event_search_entry.bind("<Control-A>", self._select_all_event_search_text)
        self.event_search_entry.bind("<Escape>", lambda _event: self._hide_transient_panels())
        self.event_search_entry.bind("<Down>", lambda _event: self._move_suggestion_selection(1))
        self.event_search_entry.bind("<Up>", lambda _event: self._move_suggestion_selection(-1))
        self.event_search_entry.bind(
            "<FocusIn>", lambda _event: self._set_suggestion_anchor(self.event_search_entry)
        )
        content = ttk.Frame(self.event_window, style="Panel.TFrame", padding=7)
        content.pack(fill=tk.BOTH, expand=True)
        self.event_tree = ttk.Treeview(
            content,
            columns=("date", "time", "event", "prediction", "print", "move", "source"),
            show="headings",
            height=8,
            style="Watchlist.Treeview",
        )
        self.event_tree.tag_configure("watchlist_even", background=WATCHLIST_ROW_EVEN)
        self.event_tree.tag_configure("watchlist_odd", background=WATCHLIST_ROW_ODD)
        self.event_tree.tag_configure("past_even", background=WATCHLIST_ROW_EVEN, foreground=MUTED)
        self.event_tree.tag_configure("past_odd", background=WATCHLIST_ROW_ODD, foreground=MUTED)
        for column, title, width in (
            ("date", "Date", 95),
            ("time", "Time (Local)", 95),
            ("event", "Event", 180),
            ("prediction", "Prediction", 120),
            ("print", "Print", 120),
            ("move", "% Move", 82),
            ("source", "Source / Note", 210),
        ):
            self.event_tree.heading(column, text=title, anchor=tk.CENTER)
            self.event_tree.column(column, width=width, anchor=tk.W)
        self.event_tree.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            content,
            textvariable=self.event_status_var,
            style="Status.TLabel",
            padding=(0, 6),
        ).pack(fill=tk.X)
        self.event_resize_grip = tk.Frame(
            self.event_window,
            bg=PANEL,
            width=15,
            height=15,
            cursor="size_nw_se",
        )
        self.event_resize_grip.place(relx=1.0, rely=1.0, anchor=tk.SE)
        self.event_resize_grip.bind("<ButtonPress-1>", self._start_event_window_resize)
        self.event_resize_grip.bind("<B1-Motion>", self._resize_event_window)
        self.event_resize_grip.bind("<ButtonRelease-1>", self._finish_event_window_resize)

    def _add_watchlist_row(self, row: dict | None = None, index: int | str = tk.END) -> str:
        item = self._next_watchlist_item_id()
        group_name = watchlist_group_name(row or {})
        if group_name:
            self.watchlist_tree.insert(
                "",
                index,
                iid=item,
                values=(watchlist_group_label(group_name), "", "", "", "", "", ""),
                tags=("watchlist_group",),
            )
            self.watchlist_groups[item] = group_name
            return item
        instrument = instrument_from_watchlist_row(row or {})
        values = watchlist_display_values(row or {}, instrument)
        row_index = (
            len(self.watchlist_tree.get_children())
            if index == tk.END
            else int(index)
        )
        self.watchlist_tree.insert(
            "", index, iid=item, values=values, tags=watchlist_item_tags(row_index, "tick_flat")
        )
        if instrument:
            self.watchlist_instruments[item] = instrument
        self._apply_watchlist_row_stripes()
        return item

    def _next_watchlist_item_id(self) -> str:
        while True:
            item = f"wl{self.watchlist_next_row_id}"
            self.watchlist_next_row_id += 1
            if not self.watchlist_tree.exists(item):
                return item

    def _remove_watchlist_row(self, item: str | None = None) -> None:
        items = (item,) if item else self.watchlist_tree.selection()
        for item in items:
            if not self.watchlist_tree.exists(item):
                continue
            self.watchlist_instruments.pop(item, None)
            self.watchlist_last_quotes.pop(item, None)
            self.watchlist_next_refresh_at.pop(item, None)
            after_id = self.watchlist_item_refresh_after_ids.pop(item, None)
            if after_id:
                self.after_cancel(after_id)
            reset_after_id = self.watchlist_tick_reset_after_ids.pop(item, None)
            if reset_after_id:
                self.after_cancel(reset_after_id)
            self.watchlist_quote_inflight.discard(item)
            self.watchlist_groups.pop(item, None)
            self.watchlist_tree.delete(item)
        if not self.watchlist_tree.get_children():
            self._add_watchlist_row()
        self._save_watchlist_state()
        self._apply_watchlist_row_stripes()

    def _show_watchlist_context_menu(self, event: tk.Event) -> str:
        self._destroy_watchlist_editor()
        item = self.watchlist_tree.identify_row(event.y)
        self.watchlist_context_item = item or None
        if item:
            self.watchlist_tree.selection_set(item)
        self.watchlist_context_menu.delete(0, tk.END)
        if item:
            self.watchlist_context_menu.add_command(
                label="Insert Row Above",
                command=lambda item=item: self._insert_watchlist_row_near(item, before=True),
            )
            self.watchlist_context_menu.add_command(
                label="Insert Row Below",
                command=lambda item=item: self._insert_watchlist_row_near(item, before=False),
            )
            self.watchlist_context_menu.add_command(
                label="Insert Group Above",
                command=lambda item=item: self._insert_watchlist_group_near(item, before=True),
            )
            self.watchlist_context_menu.add_command(
                label="Insert Group Below",
                command=lambda item=item: self._insert_watchlist_group_near(item, before=False),
            )
            if item in self.watchlist_groups:
                self.watchlist_context_menu.add_command(
                    label="Rename Group",
                    command=lambda item=item: self._rename_watchlist_group(item),
                )
            self.watchlist_context_menu.add_separator()
        else:
            self.watchlist_context_menu.add_command(
                label="Add Row At Bottom",
                command=self._append_watchlist_row_from_menu,
            )
            self.watchlist_context_menu.add_command(
                label="Add Group At Bottom",
                command=self._append_watchlist_group_from_menu,
            )
        if item:
            self.watchlist_context_menu.add_command(
                label="Remove Row",
                command=lambda item=item: self._remove_watchlist_row(item),
            )
        self.watchlist_context_menu.tk_popup(event.x_root, event.y_root)
        return "break"

    def _append_watchlist_row_from_menu(self) -> None:
        self._add_watchlist_row()
        self._save_watchlist_state()

    def _append_watchlist_group_from_menu(self) -> None:
        name = self._prompt_watchlist_group_name()
        if not name:
            return
        self._add_watchlist_group(name)
        self._save_watchlist_state()

    def _insert_watchlist_row_near(self, item: str, before: bool) -> None:
        if not self.watchlist_tree.exists(item):
            return
        target_index = self.watchlist_tree.index(item)
        insert_index = target_index if before else target_index + 1
        self._add_watchlist_row(index=insert_index)
        self._save_watchlist_state()

    def _insert_watchlist_group_near(self, item: str, before: bool) -> None:
        if not self.watchlist_tree.exists(item):
            return
        name = self._prompt_watchlist_group_name()
        if not name:
            return
        target_index = self.watchlist_tree.index(item)
        insert_index = target_index if before else target_index + 1
        self._add_watchlist_group(name, index=insert_index)
        self._save_watchlist_state()

    def _add_watchlist_group(self, name: str, index: int | str = tk.END) -> str:
        item = self._add_watchlist_row(watchlist_group_row(name), index=index)
        self._ensure_watchlist_trailing_empty_row()
        return item

    def _rename_watchlist_group(self, item: str) -> None:
        if item not in self.watchlist_groups:
            return
        name = self._prompt_watchlist_group_name(self.watchlist_groups[item])
        if not name:
            return
        self.watchlist_groups[item] = name
        self.watchlist_tree.item(
            item,
            values=(watchlist_group_label(name), "", "", "", "", "", ""),
            tags=("watchlist_group",),
        )
        self._save_watchlist_state()

    def _prompt_watchlist_group_name(self, initial: str = "") -> str:
        name = simpledialog.askstring(
            "Watchlist Group",
            "Group name:",
            initialvalue=initial or "New Group",
            parent=self,
        )
        return " ".join((name or "").strip().split())

    def _ensure_watchlist_trailing_empty_row(self) -> None:
        children = self.watchlist_tree.get_children()
        if not children or children[-1] in self.watchlist_instruments or children[-1] in self.watchlist_groups:
            self._add_watchlist_row()

    def _apply_watchlist_row_stripes(self) -> None:
        for row_index, item in enumerate(self.watchlist_tree.get_children()):
            if item in self.watchlist_groups:
                self.watchlist_tree.item(item, tags=("watchlist_group",))
                continue
            direction = next(
                (
                    tag
                    for tag in self.watchlist_tree.item(item, "tags")
                    if tag in {"tick_up", "tick_down", "tick_flat", "tick_error"}
                ),
                "tick_flat",
            )
            values = tuple(self.watchlist_tree.item(item, "values"))
            self.watchlist_tree.item(
                item,
                tags=watchlist_item_tags(
                    row_index, direction
                ),
            )

    def _position_watchlist_column_separators(self, _event: tk.Event | None = None) -> None:
        if not hasattr(self, "watchlist_column_separators"):
            return
        tree = self.watchlist_tree
        x_offset = tree.winfo_x()
        y_offset = tree.winfo_y()
        heading_height = watchlist_heading_height(tree)
        height = max(tree.winfo_height() - heading_height, 0)
        boundary = 0
        for separator, column in zip(self.watchlist_column_separators, tree["columns"][:-1]):
            boundary += int(tree.column(column, "width"))
            separator.place(
                x=x_offset + boundary - 1,
                y=y_offset + heading_height,
                width=1,
                height=height,
            )
            separator.lift(tree)

    def _start_watchlist_row_drag(self, event: tk.Event) -> None:
        if self.watchlist_editor is not None:
            return
        item = self.watchlist_tree.identify_row(event.y)
        column = self.watchlist_tree.identify_column(event.x)
        if not item or column == "#0":
            self.watchlist_drag_item = None
            return
        self.watchlist_drag_item = item
        self.watchlist_drag_start_y = event.y
        self.watchlist_drag_active = False

    def _drag_watchlist_row(self, event: tk.Event) -> str | None:
        item = self.watchlist_drag_item
        if not item:
            return None
        if abs(event.y - self.watchlist_drag_start_y) < 6 and not self.watchlist_drag_active:
            return "break"
        self.watchlist_drag_active = True
        target = self.watchlist_tree.identify_row(event.y)
        if target and target != item:
            target_index = self.watchlist_tree.index(target)
            if target in self.watchlist_groups and item not in self.watchlist_groups:
                target_index += 1
            self.watchlist_tree.move(item, "", target_index)
            self.watchlist_tree.selection_set(item)
        return "break"

    def _finish_watchlist_row_drag(self, _event: tk.Event) -> str | None:
        was_active = self.watchlist_drag_active
        if self.watchlist_drag_active:
            self._save_watchlist_state()
            self._apply_watchlist_row_stripes()
            self.status_var.set("Watchlist order saved.")
        self.watchlist_drag_item = None
        self.watchlist_drag_start_y = 0
        self.watchlist_drag_active = False
        return "break" if was_active else None

    def _finish_watchlist_column_resize(self, _event: tk.Event | None = None) -> None:
        self._position_watchlist_column_separators()
        self._mark_layout_dirty_if_changed()

    def _on_watchlist_selection_changed(self, _event: tk.Event | None = None) -> None:
        selected = self.watchlist_tree.selection()
        if not selected:
            return
        instrument = self.watchlist_instruments.get(selected[0])
        if instrument is None:
            return
        self._pause_watchlist_refresh_for_priority()
        self._open_grouped_chart_from_watchlist(instrument)
        self._schedule_grouped_events_from_watchlist(instrument)

    def _open_grouped_chart_from_watchlist(self, instrument: Instrument) -> None:
        if self.watchlist_group_var.get() != self.chart_group_var.get():
            return
        if self.chart_instruments and self.chart_instruments[0].symbol == instrument.symbol:
            return
        self.status_var.set(
            f"Group {self.watchlist_group_var.get()}: opening {instrument.symbol} in chart."
        )
        self._open_instrument(instrument)

    def _open_grouped_events_from_watchlist(self, instrument: Instrument) -> None:
        if self.watchlist_group_var.get() != self.event_group_var.get():
            return
        if self.event_instrument and self.event_instrument.symbol == instrument.symbol:
            return
        self.status_var.set(
            f"Group {self.watchlist_group_var.get()}: loading events for {instrument.symbol}."
        )
        self._open_event_calendar(instrument)

    def _schedule_grouped_events_from_watchlist(self, instrument: Instrument) -> None:
        if self.watchlist_group_var.get() != self.event_group_var.get():
            return
        if self.event_group_load_after_id:
            self.after_cancel(self.event_group_load_after_id)
        self.event_group_load_after_id = self.after(
            EVENT_GROUP_LOAD_DELAY_MS,
            lambda instrument=instrument: self._load_scheduled_grouped_events(instrument),
        )

    def _load_scheduled_grouped_events(self, instrument: Instrument) -> None:
        self.event_group_load_after_id = None
        selected = self.watchlist_tree.selection()
        if not selected:
            return
        current = self.watchlist_instruments.get(selected[0])
        if current is None or current.symbol != instrument.symbol:
            return
        self._open_grouped_events_from_watchlist(instrument)

    def _begin_watchlist_asset_search(self, event: tk.Event) -> str:
        item = self.watchlist_tree.identify_row(event.y)
        column = self.watchlist_tree.identify_column(event.x)
        if not item or column != "#1" or item in self.watchlist_groups:
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
            font=(TERMINAL_FONT_FAMILY, 10),
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
        reload_button = tk.Button(
            self.update_banner,
            text="RELOAD APP",
            command=self._reload_app,
            bg=BG,
            fg=TEXT,
            activebackground=GRID,
            activeforeground=TEXT,
            relief=tk.FLAT,
            font=(TERMINAL_FONT_FAMILY, 9, "bold"),
            padx=10,
            pady=4,
        )
        self._set_tooltip(reload_button, "Restart the app to load changed source files.")
        reload_button.pack(side=tk.RIGHT, padx=(8, 0))
        later_button = tk.Button(
            self.update_banner,
            text="LATER",
            command=self._dismiss_update_banner,
            bg=ORANGE,
            fg=BG,
            activebackground="#ffc247",
            activeforeground=BG,
            relief=tk.FLAT,
            font=(TERMINAL_FONT_FAMILY, 9),
            padx=8,
            pady=4,
        )
        self._set_tooltip(later_button, "Dismiss this update notice for now.")
        later_button.pack(side=tk.RIGHT, padx=(8, 0))

    def _poll_for_source_update(self) -> None:
        current_snapshot = source_file_snapshot(self.source_watch_paths)
        if current_snapshot != self.source_snapshot and not self.update_banner.winfo_ismapped():
            self.update_banner.place(x=18, y=58, relwidth=1.0, width=-36)
            self.update_banner.lift()
        self.after(SOURCE_WATCH_INTERVAL_MS, self._poll_for_source_update)

    def _dismiss_update_banner(self) -> None:
        self.source_snapshot = source_file_snapshot(self.source_watch_paths)
        self.update_banner.place_forget()

    def _reload_app(self) -> None:
        self._save_app_state()
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
        if self.layout_save_after_id:
            self.after_cancel(self.layout_save_after_id)
        if self.watchlist_refresh_after_id:
            self.after_cancel(self.watchlist_refresh_after_id)
        for after_id in self.watchlist_item_refresh_after_ids.values():
            self.after_cancel(after_id)
        self.watchlist_item_refresh_after_ids = {}
        if self.event_group_load_after_id:
            self.after_cancel(self.event_group_load_after_id)
        if self.watchlist_save_after_id:
            self.after_cancel(self.watchlist_save_after_id)
        self._save_app_state()
        self.destroy()

    def _save_app_state(self) -> None:
        self._save_window_state()
        self._save_watchlist_state()
        self._save_function_layout()

    def _schedule_function_layout_save(self) -> None:
        if self.layout_save_after_id:
            self.after_cancel(self.layout_save_after_id)
        self.layout_save_after_id = self.after(120, self._save_function_layout)

    def _save_function_layout(self, show_status: bool = False) -> None:
        self.layout_save_after_id = None
        self.update_idletasks()
        layout = {
            "watchlist": window_layout_state(self.watchlist_window),
            "chart": window_layout_state(self.chart_window),
            "events": window_layout_state(self.event_window),
            "macro": window_layout_state(self.macro_window),
            "news": window_layout_state(self.news_window),
            "watchlist_columns": self._watchlist_column_widths(),
            "settings": self._app_settings_state(),
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
            "watchlist": window_layout_state(self.watchlist_window),
            "chart": window_layout_state(self.chart_window),
            "events": window_layout_state(self.event_window),
            "macro": window_layout_state(self.macro_window),
            "news": window_layout_state(self.news_window),
            "watchlist_columns": self._watchlist_column_widths(),
            "settings": self._app_settings_state(),
        }

    def _app_settings_state(self) -> dict:
        return {
            "chart_mode": self.mode_var.get(),
            "selected_range": range_spec_state(self.selected_range),
            "price_render_mode": self.price_render_mode,
            "display_mode": self.display_mode_var.get(),
            "compare_panel_visible": bool(self.compare_visible_var.get()),
            "rebase_comparison": bool(self.rebase_comparison_var.get()),
            "betas_comparison": bool(self.betas_comparison_var.get()),
            "technical_study": technical_study_state(self.technical_study),
            "extended_hours": bool(self.extended_hours_var.get()),
            "intraday_custom_bar": self.intraday_custom_bar_var.get(),
            "chart_group": self.chart_group_var.get(),
            "watchlist_group": self.watchlist_group_var.get(),
            "event_group": self.event_group_var.get(),
            "macro_category": self.macro_category_var.get(),
            "news_topic": self.news_topic_var.get(),
            "search_sort": self.search_sort_var.get(),
            "chart_instruments": [
                watchlist_row_from_instrument(instrument)
                for instrument in self.chart_instruments
            ],
        }

    def _watchlist_column_widths(self) -> dict[str, int]:
        if not hasattr(self, "watchlist_tree"):
            return normalized_watchlist_column_widths({})
        return normalized_watchlist_column_widths(
            {
                column: self.watchlist_tree.column(column, "width")
                for column in self.watchlist_tree["columns"]
            }
        )

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
        self.search_sort.bind("<<ComboboxSelected>>", self._on_search_sort_changed)
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
        open_button = ttk.Button(actions, text="OPEN", command=self._accept_search_result)
        self._set_tooltip(open_button, "Open the selected search result in the active panel.")
        open_button.pack(side=tk.LEFT, padx=(0, 5))
        add_button = ttk.Button(
            actions,
            text="ADD TO COMPARE",
            style="Accent.TButton",
            command=self._add_search_result,
        )
        self._set_tooltip(add_button, "Add the selected search result as a comparison series.")
        add_button.pack(side=tk.LEFT)
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
        self._set_tooltip(self.rebase_check, "Rebase all comparison series to 100.")
        self.rebase_check.pack(side=tk.LEFT)
        self.betas_check = ttk.Checkbutton(
            mode_controls,
            text="BETAS",
            style="Chip.TCheckbutton",
            variable=self.betas_comparison_var,
            command=self._set_comparison_betas,
        )
        self._set_tooltip(self.betas_check, "Show beta regression statistics versus the primary series.")
        self.betas_check.pack(side=tk.LEFT, padx=(5, 0))
        self.beta_summary_var = tk.StringVar(value="")
        self.beta_summary_label = ttk.Label(
            panel_content, textvariable=self.beta_summary_var, style="Status.TLabel"
        )
        self.series_tree = ttk.Treeview(
            panel_content,
            columns=("symbol", "name", "venue"),
            show="headings",
            height=8,
            style="Watchlist.Treeview",
        )
        self.series_tree.tag_configure("watchlist_even", background=WATCHLIST_ROW_EVEN)
        self.series_tree.tag_configure("watchlist_odd", background=WATCHLIST_ROW_ODD)
        self.series_tree.tag_configure(
            "watchlist_group",
            background=GRID,
            foreground=ORANGE,
            font=(TERMINAL_FONT_FAMILY, 9, "bold"),
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
            textvariable=self.compare_search_var,
            bg=BG,
            fg=TEXT,
            insertbackground=TEXT,
            relief=tk.FLAT,
            font=(TERMINAL_FONT_FAMILY, 10),
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
        remove_button = ttk.Button(actions, text="REMOVE", command=self._remove_chart_series)
        self._set_tooltip(remove_button, "Remove the selected comparison series.")
        remove_button.pack(side=tk.LEFT, padx=(0, 5))
        clear_button = ttk.Button(actions, text="CLEAR", command=self._clear_chart_series)
        self._set_tooltip(clear_button, "Clear all comparison series.")
        clear_button.pack(side=tk.LEFT)

    def _select_all_search_text(self, _event: tk.Event | None = None) -> str:
        self.search_entry.selection_range(0, tk.END)
        self.search_entry.icursor(tk.END)
        return "break"

    def _select_all_event_search_text(self, _event: tk.Event | None = None) -> str:
        self.event_search_entry.selection_range(0, tk.END)
        self.event_search_entry.icursor(tk.END)
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

    def _on_compare_search_text_changed(self, *_args) -> None:
        if self.suggestion_anchor != self.compare_search_entry:
            return
        if self.search_after_id:
            self.after_cancel(self.search_after_id)
            self.search_after_id = None
        query = self.compare_search_var.get().strip()
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

    def _on_event_search_text_changed(self, *_args) -> None:
        if self.event_search_update_internal:
            return
        if self.suggestion_anchor != self.event_search_entry:
            return
        if self.search_after_id:
            self.after_cancel(self.search_after_id)
            self.search_after_id = None
        query = self.event_search_var.get().strip()
        if not query:
            self.search_request_id += 1
            self.results = []
            self.raw_results = []
            self._hide_suggestions(restore_focus=False)
            return
        self.search_after_id = self.after(SEARCH_DEBOUNCE_MS, self.search_assets)

    def _active_search_query(self) -> str:
        if self.suggestion_anchor == self.compare_search_entry:
            return self.compare_search_var.get().strip()
        if self.suggestion_anchor == self.watchlist_editor:
            return self.watchlist_search_var.get().strip()
        if self.suggestion_anchor == self.event_search_entry:
            return self.event_search_var.get().strip()
        return self.search_var.get().strip()

    def _set_suggestion_anchor(self, entry) -> None:
        self.suggestion_anchor = entry
        if entry == self.compare_search_entry:
            self.add_to_compare_mode = True
            self.search_action_var.set("ADD SECURITY TO COMPARISON")
        elif entry == self.watchlist_editor:
            self.add_to_compare_mode = False
            self.search_action_var.set("SET WATCHLIST ASSET")
        elif entry == self.event_search_entry:
            self.add_to_compare_mode = False
            self.search_action_var.set("OPEN EVENT CALENDAR")
        else:
            self.add_to_compare_mode = False
            self.search_action_var.set("OPEN SECURITY")

    def _show_suggestions(self) -> None:
        self.update_idletasks()
        anchor = self.suggestion_anchor or self.search_entry
        height = 338
        is_compare = anchor == self.compare_search_entry
        is_watchlist = anchor == self.watchlist_editor
        is_events = anchor == self.event_search_entry
        minimum_width = 420 if is_watchlist or is_events else 625 if is_compare else 720
        preferred_width = (
            max(minimum_width, anchor.winfo_width() + 320)
            if is_watchlist or is_events
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
        self._size_suggestion_columns(width, is_compare or is_watchlist or is_events)
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
        if hasattr(self, "compare_search_entry"):
            active_anchors.append(self.compare_search_entry)
        if hasattr(self, "event_search_entry"):
            active_anchors.append(self.event_search_entry)
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
            self._update_compare_button_style()
            self._begin_add_to_compare()
            return
        self._keep_primary_series_only()
        self._hide_compare_panel()

    def _toggle_compare_button(self) -> None:
        self.compare_visible_var.set(not self.compare_visible_var.get())
        self._toggle_compare_panel()

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
        self._schedule_function_layout_save()
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
        self._update_compare_button_style()
        self._redraw_current_chart()
        if self.suggestion_anchor == self.compare_search_entry:
            self._hide_suggestions(restore_focus=False)
            self.compare_search_var.set("")
            self._set_suggestion_anchor(self.search_entry)
        self._schedule_function_layout_save()
        return "break"

    def _update_compare_button_style(self) -> None:
        if hasattr(self, "compare_button"):
            self.compare_button.configure(
                style="Selected.Header.TButton"
                if self.compare_visible_var.get()
                else "Header.TButton"
            )

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
        if self.compare_search_var.get().strip() and self.results:
            self.result_tree.selection_remove(*self.result_tree.get_children())
            self.result_tree.focus("")
            self._show_suggestions()
        self.status_var.set("Type a security and select it to add to the comparison.")

    def _build_chart(self) -> None:
        self.figure = Figure(figsize=(8, 5), dpi=100, facecolor=BG)
        self.figure.subplots_adjust(left=0.035, right=0.905, top=0.985, bottom=0.085)
        grid = self.figure.add_gridspec(4, 1, hspace=0.02)
        self.price_axis = self.figure.add_subplot(grid[:3, 0])
        self.volume_axis = self.figure.add_subplot(grid[3, 0], sharex=self.price_axis)
        self.study_axis = self.volume_axis.twinx()
        self.canvas = FigureCanvasTkAgg(self.figure, master=self.chart_panel)
        self.chart_canvas_widget = self.canvas.get_tk_widget()
        self.chart_canvas_widget.pack(fill=tk.BOTH, expand=True)
        self._hide_chart_metadata_labels()
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

    def _hide_chart_metadata_labels(self) -> None:
        for label in self._chart_metadata_labels():
            label.pack_forget()
            label.place_forget()

    def _chart_metadata_labels(self) -> tuple[tk.Widget, ...]:
        return (
            self.identity_label,
            self.quote_label,
            self.fundamentals_label,
            self.sec_context_label,
            self.measurement_label,
        )

    def _build_chart_toolbar(self) -> None:
        self.chart_toolbar = tk.Frame(
            self.chart_titlebar,
            bg=GRID,
        )
        self.chart_toolbar.pack(side=tk.RIGHT, padx=(0, 4), pady=2)
        self.time_range_button = ttk.Button(
            self.chart_toolbar,
            text=self._time_range_button_label(),
            width=3,
            style="Selected.Header.TButton",
        )
        self._set_tooltip(self.time_range_button, "Open chart time range and bar interval choices.")
        self.time_range_button.pack(side=tk.LEFT)
        self.time_range_button.bind(
            "<Enter>", lambda _event: self._show_range_popup("Time Range", self.time_range_button)
        )
        self.time_range_button.bind("<Leave>", lambda _event: self._schedule_range_popup_hide())
        self.technical_button = ttk.Button(
            self.chart_toolbar,
            text="T",
            width=3,
            style="Header.TButton",
        )
        self._set_tooltip(self.technical_button, "Open technical indicator choices.")
        self.technical_button.pack(side=tk.LEFT, padx=(4, 8))
        self.technical_button.bind(
            "<Enter>", lambda _event: self._show_range_popup("Technical", self.technical_button)
        )
        self.technical_button.bind("<Leave>", lambda _event: self._schedule_range_popup_hide())
        self.extended_hours_check = ttk.Checkbutton(
            self.chart_toolbar,
            text="E",
            width=3,
            style="Header.TCheckbutton",
            variable=self.extended_hours_var,
            command=self._toggle_extended_hours,
        )
        self._set_tooltip(self.extended_hours_check, "Toggle extended-hours price data when available.")
        self.extended_hours_check.pack(side=tk.LEFT, padx=(0, 8))
        self.extended_hours_check.state(["disabled"])
        self.compare_button = ttk.Button(
            self.chart_toolbar,
            text="C",
            width=3,
            style="Header.TButton",
            command=self._toggle_compare_button,
        )
        self._set_tooltip(self.compare_button, "Open or close the comparison series panel.")
        self.compare_button.pack(side=tk.LEFT)
        self.price_render_button = ttk.Button(
            self.chart_toolbar,
            text=self._price_render_button_label(),
            width=4,
            style="Header.TButton",
            command=self._cycle_price_render_mode,
        )
        self._update_price_render_button()
        self.price_render_button.pack(side=tk.LEFT, padx=(4, 0))
        self.sec_details_button = ttk.Button(
            self.chart_toolbar,
            text="SEC",
            width=3,
            style="Header.TButton",
            command=self._show_sec_details,
        )
        self._set_tooltip(self.sec_details_button, "Open SEC filing and fundamentals details.")
        self.sec_details_button.pack(side=tk.LEFT, padx=(8, 0))
        self.sec_details_button.state(["disabled"])
        self._build_group_selector(
            self.chart_toolbar,
            self.chart_group_var,
            self._on_chart_group_changed,
        )
        self._build_range_popup()
        self._build_compare_panel()

    def _start_text_rectangle(self, event: tk.Event) -> str | None:
        if event.widget in {getattr(self, "watchlist_tree", None), self.watchlist_editor}:
            return None
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
                self._set_tooltip(
                    button,
                    f"Use {range_spec.interval} bars for the {duration} intraday window.",
                )
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
        apply_intraday_button = ttk.Button(
            intraday_custom,
            text="APPLY",
            style="Flyout.TButton",
            command=lambda: self._apply_custom_range("Intraday"),
        )
        self._set_tooltip(apply_intraday_button, "Apply the custom intraday date range and bar interval.")
        apply_intraday_button.pack(side=tk.LEFT)
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
            self._set_tooltip(button, f"Switch the chart to the {range_spec.label} historical range.")
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
        apply_historical_button = ttk.Button(
            historical_custom,
            text="APPLY DAILY",
            style="Flyout.TButton",
            command=lambda: self._apply_custom_range("Historical"),
        )
        self._set_tooltip(apply_historical_button, "Apply the custom historical daily date range.")
        apply_historical_button.pack(side=tk.LEFT)

    def _build_date_entry(self, parent, variable: tk.StringVar) -> tk.Entry:
        return tk.Entry(
            parent,
            textvariable=variable,
            width=11,
            bg=BG,
            fg=TEXT,
            insertbackground=TEXT,
            relief=tk.FLAT,
            font=(TERMINAL_FONT_FAMILY, 9),
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
                if study is None:
                    tooltip = "Show volume below the price chart."
                else:
                    tooltip = f"Show {text} technical study."
                self._set_tooltip(button, tooltip)
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

    def _restore_startup_chart_state(self) -> None:
        self._sync_custom_range_inputs()
        if len(self.chart_instruments) < 2:
            self.rebase_comparison_var.set(False)
            self.betas_comparison_var.set(False)
            self.display_mode_var.set("Prices")
        self._configure_series_tree_columns()
        self._update_series_tree()
        self._update_range_selection()
        self._update_price_render_button()
        if self.compare_visible_var.get():
            self.compare_panel.pack(
                side=tk.RIGHT,
                fill=tk.Y,
                padx=(8, 0),
                before=self.chart_canvas_widget,
            )
            self._update_compare_button_style()
            self.add_to_compare_mode = True
            self.suggestion_anchor = self.compare_search_entry
        if self.chart_instruments:
            self.after_idle(self.refresh_chart)

    def _sync_custom_range_inputs(self) -> None:
        if self.selected_range.period != "custom":
            return
        if self.mode_var.get() == "Intraday":
            self.intraday_start_var.set(self.selected_range.start or "")
            self.intraday_end_var.set(self.selected_range.end or "")
            self.intraday_custom_bar_var.set(self.selected_range.interval)
            return
        self.historical_start_var.set(self.selected_range.start or "")
        self.historical_end_var.set(self.selected_range.end or "")

    def _set_mode(self, mode: str) -> None:
        self.mode_var.set(mode)
        if mode == "Intraday":
            self.selected_range = INTRADAY_RANGES[0]
        else:
            self.selected_range = HISTORICAL_RANGES[0]
        self._update_range_selection()
        self._schedule_function_layout_save()
        if self.chart_instruments:
            self.refresh_chart()

    def _choose_range(self, range_spec: RangeSpec, mode: str | None = None) -> None:
        if mode:
            self.mode_var.set(mode)
        self.selected_range = range_spec
        self._sync_custom_range_inputs()
        self._update_range_selection()
        self._hide_range_popup()
        self._schedule_function_layout_save()
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
            style="Selected.Header.TButton",
            text=self._time_range_button_label(),
        )
        for button, range_spec in self.range_buttons:
            button.configure(
                style="Selected.Flyout.TButton"
                if range_spec == self.selected_range and self.range_popup_mode == "Time Range"
                else "Flyout.TButton"
            )
        self.technical_button.configure(
            style="Selected.Header.TButton" if self.technical_study else "Header.TButton",
            text="T",
        )
        for button, study in self.technical_buttons:
            button.configure(
                style="Selected.Flyout.TButton"
                if study == self.technical_study and self.range_popup_mode == "Technical"
                else "Flyout.TButton"
            )

    def _time_range_button_label(self) -> str:
        if self.selected_range.period == "custom":
            return "C"
        if self.selected_range.period == "1d":
            return "D"
        return self.selected_range.label.split("/")[0].strip().upper()

    def _choose_technical_study(self, study: tuple[str, int] | None) -> None:
        self.technical_study = study
        self._update_range_selection()
        self._hide_range_popup()
        self._schedule_function_layout_save()
        self._redraw_current_chart()

    def _set_comparison_rebase(self) -> None:
        self.display_mode_var.set(
            "Rebased 100" if self.rebase_comparison_var.get() else "Prices"
        )
        self._schedule_function_layout_save()
        self._redraw_current_chart()

    def _set_comparison_betas(self) -> None:
        self._update_beta_model()
        self._configure_series_tree_columns()
        self._update_series_tree()
        self._schedule_function_layout_save()

    def _toggle_extended_hours(self) -> None:
        self._schedule_function_layout_save()
        self.refresh_chart()

    def _cycle_price_render_mode(self) -> None:
        current_index = PRICE_RENDER_MODES.index(self.price_render_mode)
        self.price_render_mode = PRICE_RENDER_MODES[
            (current_index + 1) % len(PRICE_RENDER_MODES)
        ]
        self._update_price_render_button()
        self._schedule_function_layout_save()
        self._redraw_current_chart()

    def _update_price_render_button(self) -> None:
        self.price_render_button.configure(text=self._price_render_button_label())
        self._set_tooltip(
            self.price_render_button,
            f"Chart type: {PRICE_RENDER_DESCRIPTIONS[self.price_render_mode]}. Click to cycle.",
        )

    def _price_render_button_label(self) -> str:
        return PRICE_RENDER_LABELS[self.price_render_mode]

    def _configure_series_tree_columns(self) -> None:
        if self.betas_comparison_var.get():
            self.series_tree.configure(columns=BETA_SERIES_COLUMNS, height=5)
            for column in BETA_SERIES_COLUMNS:
                self.series_tree.heading(
                    column, text=BETA_SERIES_HEADINGS[column], anchor=tk.CENTER
                )
                self.series_tree.column(
                    column,
                    width=BETA_SERIES_MIN_COLUMN_WIDTH,
                    minwidth=BETA_SERIES_MIN_COLUMN_WIDTH,
                    anchor=tk.CENTER,
                    stretch=False,
                )
            self.beta_summary_label.pack(fill=tk.X, pady=(0, 5), before=self.series_tree)
            return
        self.series_tree.configure(columns=("symbol", "name", "venue"), height=8)
        for column, title, width in (
            ("symbol", "Symbol", 72),
            ("name", "Description", 128),
            ("venue", "Market", 75),
        ):
            self.series_tree.heading(column, text=title, anchor=tk.CENTER)
            self.series_tree.column(
                column,
                width=width,
                minwidth=WATCHLIST_MIN_COLUMN_WIDTH,
                anchor=tk.W if column == "name" else tk.CENTER,
                stretch=column == "name",
            )
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

    def _on_search_sort_changed(self, _event: tk.Event | None = None) -> None:
        self._schedule_function_layout_save()
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
        elif self.suggestion_anchor == self.event_search_entry:
            self._open_event_search_result()
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
            values=(watchlist_asset_label(instrument), "Loading", "", "", "", "", ""),
            tags=watchlist_item_tags(self.watchlist_tree.index(item), "tick_flat"),
        )
        self.search_action_var.set("OPEN SECURITY")
        self.watchlist_search_var.set("")
        self._ensure_watchlist_trailing_empty_row()
        self._save_watchlist_state()
        self._refresh_watchlist_item(item, instrument)

    def refresh_watchlist(self) -> None:
        for after_id in self.watchlist_item_refresh_after_ids.values():
            self.after_cancel(after_id)
        self.watchlist_item_refresh_after_ids = {}
        now = perf_counter()
        for offset, (item, instrument) in enumerate(list(self.watchlist_instruments.items())):
            if self.watchlist_next_refresh_at.get(item, 0.0) > now:
                continue
            after_id = self.after(
                offset * WATCHLIST_REFRESH_STAGGER_MS,
                lambda item=item, instrument=instrument: self._refresh_watchlist_item(item, instrument),
            )
            self.watchlist_item_refresh_after_ids[item] = after_id

    def refresh_macro_dashboard(self) -> None:
        category = self.macro_category_var.get()
        source = "FRED API" if self.macro_service.fred.uses_api_key else "FRED public CSV"
        self.macro_status_var.set(f"Loading {category} series via {source}...")
        self.macro_tree.delete(*self.macro_tree.get_children())
        self._run_background(
            lambda: self.macro_service.snapshot(category, observation_start="2018-01-01"),
            self._update_macro_dashboard,
            "Macro request failed",
        )

    def _select_macro_category(self, _value: str | None = None) -> None:
        self._populate_macro_placeholders()
        self._schedule_function_layout_save()

    def refresh_event_calendar(self) -> None:
        if self.event_instrument is None:
            self.event_status_var.set("Search or select a grouped watchlist stock to load events.")
            return
        self._load_event_calendar(self.event_instrument)

    def _open_event_search_result(self) -> None:
        instrument = self._selected_search_instrument()
        if not instrument:
            return
        self._hide_suggestions()
        self._open_event_calendar(instrument)

    def _open_event_calendar(self, instrument: Instrument) -> None:
        self.event_instrument = instrument
        self.event_search_update_internal = True
        try:
            self.event_search_var.set(instrument.symbol)
        finally:
            self.event_search_update_internal = False
        self._load_event_calendar(instrument)

    def _load_event_calendar(self, instrument: Instrument) -> None:
        self.event_request_id += 1
        request_id = self.event_request_id
        self.event_tree.delete(*self.event_tree.get_children())
        self.event_status_var.set(f"Loading public events for {instrument.symbol} via Yahoo Finance...")
        self._run_background(
            lambda: self.provider.market_events(instrument),
            lambda events: self._update_event_calendar(request_id, instrument, events),
            "Event calendar request failed",
            lambda: request_id == self.event_request_id,
            lambda exc: self._show_event_calendar_error(request_id, instrument, exc),
        )

    def _update_event_calendar(
        self,
        request_id: int,
        instrument: Instrument,
        events: list[MarketEvent],
    ) -> None:
        if request_id != self.event_request_id:
            return
        self.event_tree.delete(*self.event_tree.get_children())
        move_frame = (
            self.current_frame
            if self.selected_instrument is not None
            and self.selected_instrument.symbol == instrument.symbol
            else pd.DataFrame()
        )
        display_events = sorted(events, key=market_event_display_sort_key)
        for index, event in enumerate(display_events):
            local_date, local_time = market_event_local_date_time(event)
            source_note = event.source
            if event.note:
                source_note = f"{source_note}: {event.note}"
            move_since = event_price_move_since_text(event, move_frame)
            tags = (past_event_row_stripe(index),) if market_event_is_past(event) else (watchlist_row_stripe(index),)
            self.event_tree.insert(
                "",
                tk.END,
                iid=str(index),
                values=(
                    local_date,
                    local_time,
                    event.event,
                    event.prediction,
                    event.actual,
                    move_since,
                    source_note,
                ),
                tags=tags,
            )
        if events:
            self.event_status_var.set(
                f"{instrument.symbol}: {len(events)} public event(s). Yahoo calendar coverage is best-effort."
            )
        else:
            self.event_status_var.set(
                f"{instrument.symbol}: no Yahoo public calendar events found."
            )

    def _show_event_calendar_error(
        self,
        request_id: int,
        instrument: Instrument,
        exc: Exception,
    ) -> None:
        if request_id != self.event_request_id:
            return
        detail = str(exc).strip() or exc.__class__.__name__
        self.event_status_var.set(f"{instrument.symbol}: event calendar failed: {detail}")

    def _populate_macro_placeholders(self) -> None:
        category = self.macro_category_var.get()
        self.macro_tree.delete(*self.macro_tree.get_children())
        specs = self.macro_service.series_specs(category)
        for spec in specs:
            self.macro_tree.insert(
                "",
                tk.END,
                values=(spec.series_id, spec.title, "", "", ""),
            )
        self.macro_status_var.set(
            f"{len(specs)} configured {category} FRED series. Refresh loads public FRED CSV."
        )

    def _update_macro_dashboard(self, snapshot: MacroDashboardSnapshot) -> None:
        self.macro_tree.delete(*self.macro_tree.get_children())
        for item in snapshot.series:
            self.macro_tree.insert(
                "",
                tk.END,
                values=(
                    item.series.series_id,
                    item.series.title,
                    format_quote_value(item.latest_value),
                    format_signed_value(item.change),
                    item.latest_date.date().isoformat() if item.latest_date is not None else "",
                ),
            )
        self.macro_status_var.set(
            f"{len(snapshot.series)} {snapshot.category} series via {snapshot.source}."
        )

    def refresh_news_feed(self) -> None:
        query = news_query_by_label(self.news_topic_var.get())
        self.news_status_var.set(f"Loading {query.label} news via GDELT...")
        self.news_tree.delete(*self.news_tree.get_children())
        self._run_background(
            lambda: self.news_client.search(query),
            lambda articles: self._update_news_feed(query.label, articles),
            "News request failed",
        )

    def _select_news_topic(self, _value: str | None = None) -> None:
        self._schedule_function_layout_save()

    def _update_news_feed(self, label: str, articles: tuple[NewsArticle, ...]) -> None:
        self.news_tree.delete(*self.news_tree.get_children())
        self.news_articles = articles
        if not articles:
            self.news_tree.insert(
                "",
                tk.END,
                values=("", "GDELT", f"No {label} articles returned for this query.", ""),
            )
            self.news_status_var.set(f"No {label} articles returned via GDELT.")
            return
        for index, article in enumerate(articles):
            self.news_tree.insert(
                "",
                tk.END,
                iid=str(index),
                values=(
                    article.published_at,
                    article.source or article.language,
                    article.title,
                    article.domain,
                ),
            )
        self.news_status_var.set(f"{len(articles)} {label} articles via GDELT. Double-click to open.")

    def _show_news_error(self, label: str, exc: Exception) -> None:
        detail = str(exc).strip() or exc.__class__.__name__
        self.news_tree.delete(*self.news_tree.get_children())
        self.news_articles = ()
        self.news_tree.insert("", tk.END, values=("", "ERROR", detail, ""))
        self.news_status_var.set(f"{label}: {detail}")

    def _show_portfolio_quote_error(self, label: str, exc: Exception) -> None:
        detail = str(exc).strip() or exc.__class__.__name__
        if self.portfolio_quote_tree is not None:
            self.portfolio_quote_tree.delete(*self.portfolio_quote_tree.get_children())
            self.portfolio_quote_tree.insert("", tk.END, values=("", "", "", "", "", detail))
        self.portfolio_quote_status_var.set(f"{label}: {detail}")

    def _open_selected_news_article(self, _event: tk.Event | None = None) -> str:
        selected = self.news_tree.selection()
        if not selected or not hasattr(self, "news_articles"):
            return "break"
        article = self.news_articles[int(selected[0])]
        webbrowser.open(article.url)
        self.news_status_var.set(f"Opened: {article.title[:90]}")
        return "break"

    def _show_portfolio_quote_popup(self, _event: tk.Event | None = None) -> str:
        if self.portfolio_quote_hide_after_id:
            self.after_cancel(self.portfolio_quote_hide_after_id)
            self.portfolio_quote_hide_after_id = None
        instrument = self.selected_instrument
        if not instrument or instrument.symbol.upper() != PORTFOLIO_INDEX_SYMBOL:
            return "break"
        self._create_portfolio_quote_popup()
        self.portfolio_quote_request_id += 1
        request_id = self.portfolio_quote_request_id
        self.portfolio_quote_status_var.set("Loading constituent prices via Yahoo Finance...")
        if self.portfolio_quote_tree is not None:
            self.portfolio_quote_tree.delete(*self.portfolio_quote_tree.get_children())
        self._run_background(
            portfolio_constituent_quotes,
            lambda quotes: self._update_portfolio_quote_popup(request_id, quotes),
            "FORT_PNL constituents failed",
            lambda: request_id == self.portfolio_quote_request_id
            and self.portfolio_quote_popup is not None,
        )
        return "break"

    def _create_portfolio_quote_popup(self) -> None:
        if self.portfolio_quote_popup is not None:
            self.portfolio_quote_popup.destroy()
        popup = tk.Toplevel(self)
        popup.overrideredirect(True)
        popup.configure(bg=GRID)
        popup.transient(self)
        popup.bind("<Enter>", self._cancel_portfolio_quote_popup_hide)
        popup.bind("<Leave>", self._hide_portfolio_quote_popup)
        self.portfolio_quote_popup = popup
        anchor = getattr(self, "chart_header_summary_label", self.quote_label)
        x = anchor.winfo_rootx()
        y = anchor.winfo_rooty() + anchor.winfo_height() + 4
        popup.geometry(f"760x320+{x}+{y}")
        popup.lift()
        frame = ttk.Frame(popup, style="Panel.TFrame", padding=7)
        frame.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        ttk.Label(
            frame,
            text="FORT_PNL constituents | latest available Yahoo closes",
            style="Status.TLabel",
        ).pack(anchor=tk.W, pady=(0, 5))
        columns = ("ticker", "weight", "last", "updated", "snapshot", "name")
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=10)
        headings = {
            "ticker": "Ticker",
            "weight": "Weight",
            "last": "Last",
            "updated": "Updated",
            "snapshot": "Snapshot",
            "name": "Name",
        }
        widths = {
            "ticker": 95,
            "weight": 70,
            "last": 85,
            "updated": 95,
            "snapshot": 95,
            "name": 360,
        }
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(column, width=widths[column], anchor=tk.W, stretch=column == "name")
        tree.pack(fill=tk.BOTH, expand=True)
        tree.bind("<Enter>", self._cancel_portfolio_quote_popup_hide)
        tree.bind("<Leave>", self._hide_portfolio_quote_popup)
        ttk.Label(frame, textvariable=self.portfolio_quote_status_var, style="Status.TLabel").pack(
            anchor=tk.W, pady=(5, 0)
        )
        self.portfolio_quote_tree = tree

    def _update_portfolio_quote_popup(
        self, request_id: int, quotes: tuple[PortfolioConstituentQuote, ...]
    ) -> None:
        if request_id != self.portfolio_quote_request_id or self.portfolio_quote_tree is None:
            return
        self.portfolio_quote_tree.delete(*self.portfolio_quote_tree.get_children())
        for quote in quotes:
            self.portfolio_quote_tree.insert(
                "",
                tk.END,
                values=(
                    quote.yahoo_symbol or quote.ticker,
                    f"{quote.weight_pct:,.2f}%",
                    format_quote_value(quote.last_price),
                    quote.last_updated,
                    format_quote_value(quote.snapshot_price),
                    quote.name,
                ),
            )
        self.portfolio_quote_status_var.set(
            f"{len(quotes)} constituents | live estimate uses current weights, not broker refresh."
        )

    def _cancel_portfolio_quote_popup_hide(self, _event: tk.Event | None = None) -> str:
        if self.portfolio_quote_hide_after_id:
            self.after_cancel(self.portfolio_quote_hide_after_id)
            self.portfolio_quote_hide_after_id = None
        return "break"

    def _hide_portfolio_quote_popup(self, _event: tk.Event | None = None) -> str:
        if self.portfolio_quote_hide_after_id:
            self.after_cancel(self.portfolio_quote_hide_after_id)
        self.portfolio_quote_hide_after_id = self.after(450, self._destroy_portfolio_quote_popup)
        return "break"

    def _destroy_portfolio_quote_popup(self) -> None:
        self.portfolio_quote_hide_after_id = None
        self.portfolio_quote_request_id += 1
        if self.portfolio_quote_popup is not None:
            self.portfolio_quote_popup.destroy()
        self.portfolio_quote_popup = None
        self.portfolio_quote_tree = None

    def _save_watchlist_state(self) -> None:
        self.watchlist_save_after_id = None
        rows = []
        for item in self.watchlist_tree.get_children():
            if item in self.watchlist_groups:
                rows.append(watchlist_group_row(self.watchlist_groups[item]))
            else:
                instrument = self.watchlist_instruments.get(item)
                row = watchlist_row_from_instrument(instrument)
                if row:
                    row["display_values"] = list(self.watchlist_tree.item(item, "values"))
                rows.append(row)
        save_watchlist_state(self.watchlist_state_path, rows)

    def _schedule_watchlist_state_save(self) -> None:
        if self.watchlist_save_after_id:
            self.after_cancel(self.watchlist_save_after_id)
        self.watchlist_save_after_id = self.after(250, self._save_watchlist_state)

    def _schedule_watchlist_refresh_loop(self) -> None:
        self.watchlist_refresh_after_id = None
        self.refresh_watchlist()
        self.watchlist_refresh_after_id = self.after(
            WATCHLIST_REFRESH_INTERVAL_MS, self._schedule_watchlist_refresh_loop
        )

    def _pause_watchlist_refresh_for_priority(self) -> None:
        if self.watchlist_refresh_after_id:
            self.after_cancel(self.watchlist_refresh_after_id)
            self.watchlist_refresh_after_id = None
        for after_id in self.watchlist_item_refresh_after_ids.values():
            self.after_cancel(after_id)
        self.watchlist_item_refresh_after_ids = {}
        self.watchlist_refresh_after_id = self.after(
            WATCHLIST_PRIORITY_PAUSE_MS, self._schedule_watchlist_refresh_loop
        )

    def _refresh_watchlist_item(self, item: str, instrument: Instrument) -> None:
        self.watchlist_item_refresh_after_ids.pop(item, None)
        if item not in self.watchlist_instruments:
            return
        if self.watchlist_next_refresh_at.get(item, 0.0) > perf_counter():
            return
        if item in self.watchlist_quote_inflight:
            return
        self.watchlist_quote_inflight.add(item)
        self._run_background(
            lambda: self._fetch_watchlist_quote(instrument),
            lambda result: self._update_watchlist_quote(item, instrument, result[0], result[1]),
            "Watchlist quote failed",
            lambda: item in self.watchlist_instruments
            and self.watchlist_instruments[item].symbol == instrument.symbol,
            lambda exc: self._update_watchlist_quote_error(item, instrument, exc),
        )

    def _fetch_watchlist_quote(self, instrument: Instrument):
        start = perf_counter()
        quote = self.provider.quote_snapshot(instrument, include_slow_info=False)
        latency_ms = (perf_counter() - start) * 1000
        return quote, latency_ms

    def _update_watchlist_quote(self, item: str, instrument: Instrument, quote, latency_ms: float) -> None:
        self.watchlist_quote_inflight.discard(item)
        direction_tag, is_tick_flash = self._watchlist_tick_tag(item, quote)
        values = (
            watchlist_asset_label(instrument),
            format_quote_value(quote.last),
            format_bid_ask_value(quote.bid, quote, "bid"),
            format_bid_ask_value(quote.ask, quote, "ask"),
            format_quote_change(quote.change, quote.change_percent),
            format_volume_value(quote.volume) if quote.volume is not None else "",
            format_latency_value(latency_ms),
        )
        self.watchlist_tree.item(
            item,
            values=values,
            tags=watchlist_item_tags(
                self.watchlist_tree.index(item),
                direction_tag,
            ),
        )
        if quote_allows_realtime_refresh(quote):
            self.watchlist_next_refresh_at.pop(item, None)
        else:
            self.watchlist_next_refresh_at[item] = (
                perf_counter() + WATCHLIST_CLOSED_REFRESH_INTERVAL_MS / 1000
            )
        self._schedule_watchlist_state_save()
        self._schedule_watchlist_tick_color_reset(item, quote, is_tick_flash)

    def _watchlist_tick_tag(self, item: str, quote) -> tuple[str, bool]:
        current = (quote.last, quote.bid, quote.ask)
        previous = self.watchlist_last_quotes.get(item)
        self.watchlist_last_quotes[item] = current
        if previous is not None:
            for old, new in zip(previous, current):
                if old is None or new is None or old == new:
                    continue
                return ("tick_up" if new > old else "tick_down"), True
        return quote_change_tag(quote.change, quote.change_percent), False

    def _schedule_watchlist_tick_color_reset(self, item: str, quote, is_tick_flash: bool) -> None:
        reset_after_id = self.watchlist_tick_reset_after_ids.pop(item, None)
        if reset_after_id:
            self.after_cancel(reset_after_id)
        if not is_tick_flash:
            return
        fallback_tag = quote_change_tag(quote.change, quote.change_percent)

        def reset_color() -> None:
            self.watchlist_tick_reset_after_ids.pop(item, None)
            if item not in self.watchlist_instruments or not self.watchlist_tree.exists(item):
                return
            self.watchlist_tree.item(
                item,
                tags=watchlist_item_tags(self.watchlist_tree.index(item), fallback_tag),
            )

        self.watchlist_tick_reset_after_ids[item] = self.after(
            WATCHLIST_TICK_FLASH_MS, reset_color
        )

    def _update_watchlist_quote_error(
        self, item: str, instrument: Instrument, exc: Exception
    ) -> None:
        self.watchlist_quote_inflight.discard(item)
        if item not in self.watchlist_instruments:
            return
        self.watchlist_next_refresh_at[item] = (
            perf_counter() + WATCHLIST_REFRESH_INTERVAL_MS / 1000
        )
        detail = str(exc).strip()[:18] or exc.__class__.__name__
        current_values = list(self.watchlist_tree.item(item, "values"))
        if len(current_values) == len(WATCHLIST_COLUMNS) and any(current_values[1:6]):
            current_values[-1] = detail
            self.watchlist_tree.item(
                item,
                values=tuple(current_values),
                tags=watchlist_item_tags(self.watchlist_tree.index(item), "tick_error"),
            )
            self._schedule_watchlist_state_save()
            return
        self.watchlist_tree.item(
            item,
            values=(
                watchlist_asset_label(instrument),
                "ERR",
                "",
                "",
                "",
                "",
                detail,
            ),
            tags=watchlist_item_tags(self.watchlist_tree.index(item), "tick_error"),
        )
        self._schedule_watchlist_state_save()

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
        self.compare_search_var.set("")
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
        self._schedule_function_layout_save()
        self.refresh_chart()

    def _open_instrument(self, instrument: Instrument) -> None:
        self.chart_instruments = [instrument]
        self.selected_instrument = instrument
        self.rebase_comparison_var.set(False)
        self.betas_comparison_var.set(False)
        self.display_mode_var.set("Prices")
        self._update_series_tree()
        self._schedule_function_layout_save()
        self.refresh_chart()

    def _update_series_tree(self) -> None:
        self.series_tree.delete(*self.series_tree.get_children())
        if self.betas_comparison_var.get():
            self._populate_beta_series_tree()
            self._update_compare_button_style()
            return
        for position, instrument in enumerate(self.chart_instruments):
            self.series_tree.insert(
                "",
                tk.END,
                iid=str(position),
                values=(instrument.symbol, instrument.name, instrument.exchange),
                tags=(watchlist_row_stripe(position),),
            )
        self._update_compare_button_style()

    def _populate_beta_series_tree(self) -> None:
        if not self.chart_instruments:
            self.beta_summary_var.set("Open a primary series, then add X series.")
            self._fit_beta_series_columns()
            return
        primary = self.chart_instruments[0]
        stats = self.beta_model_stats
        self.series_tree.insert(
            "",
            tk.END,
            iid="0",
            values=(f"Y: {primary.symbol}", "", "", "", ""),
            tags=(watchlist_row_stripe(0),),
        )
        if not stats:
            self.beta_summary_var.set("Add X series and load enough aligned returns.")
            for position, instrument in enumerate(self.chart_instruments[1:], start=1):
                self.series_tree.insert(
                    "",
                    tk.END,
                    iid=str(position),
                    values=(instrument.symbol, "-", "-", "-", "-"),
                    tags=(watchlist_row_stripe(position),),
                )
            self._fit_beta_series_columns()
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
            self.series_tree.insert(
                "",
                tk.END,
                iid=str(position),
                values=values,
                tags=(watchlist_row_stripe(position),),
            )
        self._fit_beta_series_columns()

    def _fit_beta_series_columns(self) -> None:
        if not self.betas_comparison_var.get():
            return
        rows = [
            tuple(str(value) for value in self.series_tree.item(item, "values"))
            for item in self.series_tree.get_children()
        ]
        width = beta_table_column_width(rows)
        for column in BETA_SERIES_COLUMNS:
            self.series_tree.column(
                column,
                width=width,
                minwidth=BETA_SERIES_MIN_COLUMN_WIDTH,
                anchor=tk.CENTER,
                stretch=False,
            )

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
        self._schedule_function_layout_save()
        self.refresh_chart()

    def _clear_chart_series(self) -> None:
        self.chart_instruments = []
        self.selected_instrument = None
        self._update_series_tree()
        self._hide_compare_panel()
        self._clear_chart("Search for an asset, then choose Open or Add.")
        self.identity_var.set("")
        self.quote_var.set("")
        self.chart_header_summary_var.set("")
        self.fundamentals_var.set("")
        self.sec_context_var.set("")
        self.session_var.set("")
        self.hours_var.set("")
        self.status_var.set("Chart series cleared.")
        self._schedule_function_layout_save()

    def refresh_chart(self) -> None:
        if not self.chart_instruments:
            return
        self._cancel_chart_live_quote_refresh()
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
        self.current_series_colors = comparison_series_colors(instruments, visible_frames)
        if self.chart_group_var.get() == self.event_group_var.get():
            if self.event_instrument is None or self.event_instrument.symbol != primary.symbol:
                self._open_event_calendar(primary)
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
            frame_for_symbol = visible_frames[instrument.symbol]
            color = self.current_series_colors.get(
                instrument.symbol,
                comparison_series_color(frame_for_symbol, position, len(instruments)),
            )
            if position == 0 and len(instruments) == 1 and self.display_mode_var.get() == "Prices":
                self._draw_primary_price_series(frame_for_symbol, instrument.symbol, color)
            else:
                closes = plot_frames[instrument.symbol]
                self.price_axis.plot(
                    closes.index,
                    closes,
                    color=color,
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
        self.identity_var.set(instrument_identity_text(primary))
        self.quote_var.set("")
        self.chart_header_summary_var.set(
            chart_header_summary_text(primary, frame, last, change, pct, currency)
        )
        self.fundamentals_var.set(instrument_fundamentals_text(primary))
        self._refresh_sec_context(primary)
        ylabel = comparison_y_axis_label(
            [instrument.symbol for instrument in instruments],
            self.display_mode_var.get(),
        )
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
        if len(instruments) > 1:
            self._draw_comparison_latest_value_markers(plot_frames, self.current_series_colors)
        else:
            self._draw_latest_value_markers(frame, self._selected_period_change_color(frame))
        self._apply_chart_font()
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
        self._refresh_chart_live_quote(primary, frame)

    def _refresh_chart_live_quote(self, instrument: Instrument, frame: pd.DataFrame) -> None:
        self.chart_quote_after_id = None
        self.chart_quote_request_id += 1
        request_id = self.chart_quote_request_id
        self._run_background(
            lambda: self.provider.quote_snapshot(instrument, include_slow_info=False),
            lambda quote: self._update_chart_live_quote(request_id, instrument, frame, quote),
            "Live quote failed",
            lambda: request_id == self.chart_quote_request_id
            and self.selected_instrument is not None
            and self.selected_instrument.symbol == instrument.symbol,
            lambda _exc: self._schedule_chart_live_quote_refresh(request_id, instrument, frame),
        )

    def _update_chart_live_quote(
        self,
        request_id: int,
        instrument: Instrument,
        frame: pd.DataFrame,
        quote,
    ) -> None:
        if (
            request_id != self.chart_quote_request_id
            or self.selected_instrument is None
            or self.selected_instrument.symbol != instrument.symbol
        ):
            return
        text = chart_live_quote_summary_text(instrument, frame, quote)
        if text:
            self.chart_header_summary_var.set(text)
            self.quote_var.set(quote_source_text(quote))
        self._schedule_chart_live_quote_refresh(request_id, instrument, frame)

    def _schedule_chart_live_quote_refresh(
        self,
        request_id: int,
        instrument: Instrument,
        frame: pd.DataFrame,
    ) -> None:
        if (
            request_id != self.chart_quote_request_id
            or self.selected_instrument is None
            or self.selected_instrument.symbol != instrument.symbol
        ):
            return
        self._cancel_chart_live_quote_refresh()
        self.chart_quote_after_id = self.after(
            CHART_LIVE_QUOTE_INTERVAL_MS,
            lambda: self._refresh_chart_live_quote(instrument, frame),
        )

    def _cancel_chart_live_quote_refresh(self) -> None:
        if self.chart_quote_after_id:
            self.after_cancel(self.chart_quote_after_id)
            self.chart_quote_after_id = None

    def _refresh_sec_context(self, instrument: Instrument) -> None:
        symbol = instrument.symbol.strip().upper()
        self.sec_context = None
        self.sec_details_button.state(["disabled"])
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

    def _update_sec_context(self, request_id: int, context: SecCompanyContext | None) -> None:
        if request_id != self.chart_request_id:
            return
        self.sec_context = context
        if context:
            self.sec_details_button.state(["!disabled"])
        self.sec_context_var.set(format_sec_company_context(context) if context else "")

    def _show_sec_details(self) -> None:
        context = self.sec_context
        if context is None:
            self.status_var.set("SEC details are not loaded for the selected ticker.")
            return
        window = tk.Toplevel(self)
        window.title(f"SEC Details | {context.company.ticker}")
        window.configure(bg=BG)
        window.geometry("860x520")
        ttk.Label(
            window,
            text=f"{context.company.title} | CIK {context.company.cik}",
            style="Quote.TLabel",
            padding=(12, 10, 12, 4),
        ).pack(anchor=tk.W, fill=tk.X)

        facts = ttk.Treeview(
            window,
            columns=("tag", "value", "period", "filed", "form"),
            show="headings",
            height=8,
        )
        for column, label, width in (
            ("tag", "Fact", 220),
            ("value", "Value", 160),
            ("period", "Period", 110),
            ("filed", "Filed", 110),
            ("form", "Form", 80),
        ):
            facts.heading(column, text=label)
            facts.column(column, width=width, anchor=tk.W)
        for fact in context.fundamentals.facts[:12]:
            facts.insert(
                "",
                tk.END,
                values=(
                    fact.tag,
                    f"{fact.value} {fact.unit}",
                    fact.fiscal_period or fact.end,
                    fact.filed,
                    fact.form,
                ),
            )
        facts.pack(fill=tk.X, padx=12, pady=(4, 10))

        filings = ttk.Treeview(
            window,
            columns=("form", "filed", "report", "document"),
            show="headings",
            height=8,
        )
        for column, label, width in (
            ("form", "Form", 90),
            ("filed", "Filed", 110),
            ("report", "Report", 110),
            ("document", "Primary Document", 420),
        ):
            filings.heading(column, text=label)
            filings.column(column, width=width, anchor=tk.W)
        for index, filing in enumerate(context.filings):
            filings.insert(
                "",
                tk.END,
                iid=str(index),
                values=(
                    filing.form,
                    filing.filing_date,
                    filing.report_date,
                    filing.primary_document,
                ),
            )
        filings.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))

        def open_selected_filing() -> None:
            selected = filings.selection()
            if not selected:
                self.status_var.set("Select an SEC filing to open.")
                return
            filing = context.filings[int(selected[0])]
            webbrowser.open(filing.filing_url)
            self.status_var.set(f"Opened SEC filing {filing.form} {filing.filing_date}.")

        def clear_sec_cache() -> None:
            self.sec_client.clear_cache()
            self.sec_context = None
            self.sec_context_var.set("")
            self.sec_details_button.state(["disabled"])
            window.destroy()
            self.status_var.set("SEC cache cleared. Reload the chart to fetch fresh SEC data.")

        filings.bind("<Double-1>", lambda _event: open_selected_filing())
        actions = ttk.Frame(window, padding=(12, 0, 12, 12))
        actions.pack(fill=tk.X)
        clear_cache_button = ttk.Button(
            actions,
            text="CLEAR SEC CACHE",
            style="Chip.TButton",
            command=clear_sec_cache,
        )
        self._set_tooltip(clear_cache_button, "Clear cached SEC data and fetch fresh data next time.")
        clear_cache_button.pack(side=tk.LEFT)
        open_filing_button = ttk.Button(
            actions,
            text="OPEN SELECTED FILING",
            style="Accent.TButton",
            command=open_selected_filing,
        )
        self._set_tooltip(open_filing_button, "Open the selected SEC filing in your browser.")
        open_filing_button.pack(side=tk.RIGHT)

    def _draw_primary_price_series(self, frame: pd.DataFrame, symbol: str, color: str) -> None:
        ohlc_columns = {"Open", "High", "Low", "Close"}
        hlc_columns = {"High", "Low", "Close"}
        if self.price_render_mode == "bars" and ohlc_columns.issubset(frame.columns):
            self._draw_ohlc_bars(frame, symbol, color)
            return
        if self.price_render_mode == "candles" and ohlc_columns.issubset(frame.columns):
            self._draw_candles(frame, symbol, color, hollow=False)
            return
        if self.price_render_mode == "hollow_candles" and ohlc_columns.issubset(frame.columns):
            self._draw_candles(frame, symbol, color, hollow=True)
            return
        if self.price_render_mode == "hlc_bars" and hlc_columns.issubset(frame.columns):
            self._draw_high_low_close_bars(frame, symbol, color)
            return
        if self.price_render_mode == "step_line":
            drawstyle = "steps-post"
            marker = None
        else:
            drawstyle = "default"
            marker = "o" if self.price_render_mode == "line_markers" else None
        self.price_axis.plot(
            frame.index,
            frame["Close"],
            color=color,
            linewidth=1.5,
            marker=marker,
            markersize=3.2 if marker else None,
            drawstyle=drawstyle,
            label=self._primary_legend_label(symbol),
        )

    def _primary_legend_label(self, symbol: str) -> str:
        return f"{symbol} | {PRICE_RENDER_DESCRIPTIONS.get(self.price_render_mode, 'price')}"

    def _draw_ohlc_bars(self, frame: pd.DataFrame, symbol: str, color: str) -> None:
        data = frame[["Open", "High", "Low", "Close"]].apply(pd.to_numeric, errors="coerce").dropna()
        if data.empty:
            return
        width = _bar_width(data) * 0.35
        x_values = mdates.date2num(data.index.to_pydatetime())
        colors = [UP if row["Close"] >= row["Open"] else DOWN for _, row in data.iterrows()]
        self.price_axis.vlines(data.index, data["Low"], data["High"], color=colors, linewidth=0.9)
        self.price_axis.hlines(
            data["Open"],
            x_values - width,
            x_values,
            color=colors,
            linewidth=1.0,
        )
        self.price_axis.hlines(
            data["Close"],
            x_values,
            x_values + width,
            color=colors,
            linewidth=1.0,
        )
        self.price_axis.plot([], [], color=color, linewidth=1.4, label=self._primary_legend_label(symbol))

    def _draw_high_low_close_bars(self, frame: pd.DataFrame, symbol: str, color: str) -> None:
        data = frame[["High", "Low", "Close"]].apply(pd.to_numeric, errors="coerce").dropna()
        if data.empty:
            return
        width = _bar_width(data) * 0.35
        x_values = mdates.date2num(data.index.to_pydatetime())
        self.price_axis.vlines(data.index, data["Low"], data["High"], color=MUTED, linewidth=0.9)
        self.price_axis.hlines(
            data["Close"],
            x_values - width / 2,
            x_values + width / 2,
            color=color,
            linewidth=1.0,
            label=self._primary_legend_label(symbol),
        )

    def _draw_candles(self, frame: pd.DataFrame, symbol: str, color: str, hollow: bool) -> None:
        data = frame[["Open", "High", "Low", "Close"]].apply(pd.to_numeric, errors="coerce").dropna()
        if data.empty:
            return
        width = _bar_width(data) * 0.55
        for timestamp, row in data.iterrows():
            open_price = float(row["Open"])
            close_price = float(row["Close"])
            candle_color = UP if close_price >= open_price else DOWN
            self.price_axis.vlines(
                timestamp,
                row["Low"],
                row["High"],
                color=candle_color if hollow else MUTED,
                linewidth=0.8,
            )
            low = min(open_price, close_price)
            height = max(abs(close_price - open_price), 1e-9)
            facecolor = PANEL if hollow and close_price >= open_price else candle_color
            left = mdates.date2num(timestamp) - width / 2
            self.price_axis.add_patch(
                Rectangle(
                    (left, low),
                    width,
                    height,
                    facecolor=facecolor,
                    edgecolor=candle_color,
                    linewidth=0.7,
                    alpha=1.0 if hollow else 0.82,
                )
            )
        self.price_axis.plot([], [], color=color, linewidth=1.4, label=self._primary_legend_label(symbol))

    def _selected_period_change_color(self, frame: pd.DataFrame) -> str:
        closes = pd.to_numeric(frame.get("Close", pd.Series(dtype=float)), errors="coerce").dropna()
        if len(closes) < 2:
            return MUTED
        change = float(closes.iloc[-1] - closes.iloc[0])
        if change > 0:
            return UP
        if change < 0:
            return DOWN
        return MUTED

    def _draw_latest_value_markers(self, frame: pd.DataFrame, color: str) -> None:
        closes = pd.to_numeric(frame.get("Close", pd.Series(dtype=float)), errors="coerce").dropna()
        if closes.empty:
            return
        latest_close = float(closes.iloc[-1])
        self.price_axis.axhline(
            latest_close,
            color=color,
            linewidth=0.85,
            linestyle="-",
            alpha=0.72,
            zorder=1,
        )
        self.price_axis.annotate(
            format_quote_value(latest_close),
            xy=(1.01, latest_close),
            xycoords=("axes fraction", "data"),
            xytext=(0, 0),
            textcoords="offset points",
            ha="left",
            va="center",
            color=BG,
            fontsize=8,
            fontweight="bold",
            annotation_clip=False,
            clip_on=False,
            bbox={
                "boxstyle": "round,pad=0.22",
                "facecolor": color,
                "edgecolor": color,
                "linewidth": 0.0,
                "alpha": 0.95,
            },
            zorder=6,
        )
        if "Volume" not in frame.columns:
            return
        volumes = pd.to_numeric(frame["Volume"], errors="coerce").dropna()
        if volumes.empty:
            return
        latest_volume = float(volumes.iloc[-1])
        self.volume_axis.annotate(
            format_volume_value(latest_volume),
            xy=(1.01, latest_volume),
            xycoords=("axes fraction", "data"),
            xytext=(0, 0),
            textcoords="offset points",
            ha="left",
            va="center",
            color=BG,
            fontsize=8,
            fontweight="bold",
            annotation_clip=False,
            clip_on=False,
            bbox={
                "boxstyle": "round,pad=0.22",
                "facecolor": color,
                "edgecolor": color,
                "linewidth": 0.0,
                "alpha": 0.95,
            },
            zorder=6,
        )

    def _draw_comparison_latest_value_markers(
        self, series: dict[str, pd.Series], colors: dict[str, str]
    ) -> None:
        for position, (symbol, closes) in enumerate(series.items()):
            values = pd.to_numeric(closes, errors="coerce").dropna()
            if values.empty:
                continue
            latest_value = float(values.iloc[-1])
            color = colors.get(symbol, SERIES_COLORS[position % len(SERIES_COLORS)])
            self.price_axis.axhline(
                latest_value,
                color=color,
                linewidth=0.72,
                linestyle="-",
                alpha=0.45 if position else 0.62,
                zorder=1,
            )
            self.price_axis.annotate(
                comparison_latest_value_label(symbol, latest_value),
                xy=(1.01, latest_value),
                xycoords=("axes fraction", "data"),
                xytext=(0, 0),
                textcoords="offset points",
                ha="left",
                va="center",
                color=BG,
                fontsize=8,
                fontweight="bold",
                annotation_clip=False,
                clip_on=False,
                bbox={
                    "boxstyle": "round,pad=0.22",
                    "facecolor": color,
                    "edgecolor": color,
                    "linewidth": 0.0,
                    "alpha": 0.95,
                },
                zorder=6 + position,
            )

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
            or event.ydata is None
            or not self.current_frames
            or self.measurement_mode
        ):
            self._hide_hover()
            return
        self._draw_crosshair_hover(event.xdata, event.ydata)

    def _series_display_color(self, symbol: str, position: int) -> str:
        if symbol in self.current_series_colors:
            return self.current_series_colors[symbol]
        frame = self.current_frames.get(symbol)
        return comparison_series_color(frame, position, len(self.current_frames))

    def _draw_hover(self, timestamp: pd.Timestamp, values: list[tuple[str, float]]) -> None:
        self._clear_hover()
        guide = self.price_axis.axvline(timestamp, color=MUTED, linewidth=0.8, linestyle="--")
        self.hover_artists.append(guide)
        for position, (symbol, value) in enumerate(values):
            marker = self.price_axis.scatter(
                [timestamp],
                [value],
                color=self._series_display_color(symbol, position),
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

    def _draw_crosshair_hover(self, x_position: float, y_position: float) -> None:
        self._clear_hover()
        timestamp = mdates.num2date(x_position)
        vertical = self.price_axis.axvline(
            x_position, color=MUTED, linewidth=0.8, linestyle="--", alpha=0.86, zorder=5
        )
        horizontal = self.price_axis.axhline(
            y_position, color=MUTED, linewidth=0.8, linestyle="--", alpha=0.86, zorder=5
        )
        self.hover_artists.extend([vertical, horizontal])
        price_label = self.price_axis.annotate(
            format_quote_value(float(y_position)),
            xy=(1.01, y_position),
            xycoords=("axes fraction", "data"),
            xytext=(0, 0),
            textcoords="offset points",
            ha="left",
            va="center",
            color=BG,
            fontsize=8,
            fontweight="bold",
            fontfamily=TERMINAL_FONT_FAMILY,
            annotation_clip=False,
            clip_on=False,
            bbox={
                "boxstyle": "round,pad=0.22",
                "facecolor": ORANGE,
                "edgecolor": ORANGE,
                "linewidth": 0.0,
                "alpha": 0.95,
            },
            zorder=8,
        )
        time_label = self.volume_axis.annotate(
            self._format_chart_time(pd.Timestamp(timestamp)),
            xy=(x_position, -0.03),
            xycoords=("data", "axes fraction"),
            xytext=(0, 0),
            textcoords="offset points",
            ha="center",
            va="top",
            color=BG,
            fontsize=8,
            fontweight="bold",
            fontfamily=TERMINAL_FONT_FAMILY,
            annotation_clip=False,
            clip_on=False,
            bbox={
                "boxstyle": "round,pad=0.22",
                "facecolor": ORANGE,
                "edgecolor": ORANGE,
                "linewidth": 0.0,
                "alpha": 0.95,
            },
            zorder=8,
        )
        self.hover_artists.extend([price_label, time_label])
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
        if (
            self.display_mode_var.get() == "Prices"
            and len(self.current_frames) == 1
            and self.price_render_mode in {"bars", "candles", "hollow_candles", "hlc_bars"}
        ):
            frame = next(iter(self.current_frames.values()))
            columns = [column for column in ("Low", "High", "Close") if column in frame.columns]
            displayed = {
                "primary": pd.concat(
                    [pd.to_numeric(frame[column], errors="coerce") for column in columns]
                )
            }
        else:
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
        for label in self.volume_axis.get_xticklabels():
            label.set_rotation(0)
            label.set_ha("center")
        self.figure.subplots_adjust(left=0.035, right=0.905, top=0.985, bottom=0.085)

    def _clear_chart(self, text: str) -> None:
        self._cancel_chart_live_quote_refresh()
        self.current_frame = pd.DataFrame()
        self.current_frames = {}
        self.current_series_colors = {}
        self._clear_hover()
        self._clear_measurement()
        self.quote_var.set("")
        self.chart_header_summary_var.set("")
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
            axis.yaxis.tick_right()
            axis.yaxis.set_label_position("right")
            axis.xaxis.label.set_fontfamily(TERMINAL_FONT_FAMILY)
            axis.yaxis.label.set_fontfamily(TERMINAL_FONT_FAMILY)
            axis.grid(True, color=GRID, linewidth=0.55, alpha=0.8)
            for spine in axis.spines.values():
                spine.set_color(GRID)
            axis.spines["left"].set_visible(False)
            axis.spines["right"].set_visible(True)
        self.study_axis.set_visible(False)
        self.study_axis.tick_params(colors=ORANGE, labelsize=9)
        self.study_axis.xaxis.label.set_fontfamily(TERMINAL_FONT_FAMILY)
        self.study_axis.yaxis.label.set_fontfamily(TERMINAL_FONT_FAMILY)
        self.study_axis.spines["right"].set_color(ORANGE)
        for spine_name in ("left", "top", "bottom"):
            self.study_axis.spines[spine_name].set_visible(False)
        self.study_axis.grid(False)
        self.price_axis.tick_params(labelbottom=False)
        self._apply_chart_font()

    def _apply_chart_font(self) -> None:
        for axis in (self.price_axis, self.volume_axis, self.study_axis):
            axis.title.set_fontfamily(TERMINAL_FONT_FAMILY)
            axis.xaxis.label.set_fontfamily(TERMINAL_FONT_FAMILY)
            axis.yaxis.label.set_fontfamily(TERMINAL_FONT_FAMILY)
            for label in (*axis.get_xticklabels(), *axis.get_yticklabels()):
                label.set_fontfamily(TERMINAL_FONT_FAMILY)
            for text in axis.texts:
                text.set_fontfamily(TERMINAL_FONT_FAMILY)
            legend = axis.get_legend()
            if legend is not None:
                for text in legend.get_texts():
                    text.set_fontfamily(TERMINAL_FONT_FAMILY)

    def _run_background(
        self,
        function,
        on_success,
        label: str,
        is_current=lambda: True,
        on_error=None,
    ) -> None:
        def worker() -> None:
            try:
                result = function()
            except Exception as exc:  # network/provider errors belong in the status bar.
                error = exc
                self.after(
                    0,
                    lambda: (
                        on_error(error)
                        if on_error is not None and is_current()
                        else self._show_error(label, error)
                        if is_current()
                        else None
                    ),
                )
                return
            self.after(0, lambda: on_success(result))

        threading.Thread(target=worker, daemon=True).start()

    def _show_error(self, label: str, exc: Exception) -> None:
        detail = str(exc).strip() or exc.__class__.__name__
        if label == "News request failed":
            self._show_news_error(label, exc)
            return
        if label == "FORT_PNL constituents failed":
            self._show_portfolio_quote_error(label, exc)
            return
        self.status_var.set(f"{label}: {detail}")
        if isinstance(exc, GdeltRateLimitError):
            self.news_status_var.set(detail)
            return
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
    rows = _load_watchlist_state_file(path)
    if rows:
        return rows
    return _load_watchlist_state_file(path.with_suffix(path.suffix + ".bak"))


def _load_watchlist_state_file(path: Path) -> list[dict]:
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


def normalized_app_settings(payload: object) -> dict:
    settings = payload if isinstance(payload, dict) else {}
    chart_mode = _choice(settings.get("chart_mode"), {"Intraday", "Historical"}, "Intraday")
    selected_range = range_spec_from_state(settings.get("selected_range"), chart_mode)
    technical_study = technical_study_from_state(settings.get("technical_study"))
    chart_instruments = [
        instrument
        for instrument in (
            instrument_from_watchlist_row(row)
            for row in settings.get("chart_instruments", [])
            if isinstance(row, dict)
        )
        if instrument is not None
    ]
    has_comparison = len(chart_instruments) >= 2
    display_mode = _choice(settings.get("display_mode"), {"Prices", "Rebased 100"}, "Prices")
    return {
        "chart_mode": chart_mode,
        "selected_range": selected_range,
        "price_render_mode": _choice(
            settings.get("price_render_mode"), set(PRICE_RENDER_MODES), "bars"
        ),
        "display_mode": display_mode if has_comparison else "Prices",
        "compare_panel_visible": bool(settings.get("compare_panel_visible", False)) and has_comparison,
        "rebase_comparison": bool(settings.get("rebase_comparison", False)) and has_comparison,
        "betas_comparison": bool(settings.get("betas_comparison", False)) and has_comparison,
        "technical_study": technical_study,
        "extended_hours": bool(settings.get("extended_hours", False)),
        "intraday_custom_bar": _choice(
            settings.get("intraday_custom_bar"),
            {"1m", "5m", "15m", "30m", "60m"},
            "15m",
        ),
        "chart_group": _choice(settings.get("chart_group"), set("ABCDEF"), "A"),
        "watchlist_group": _choice(settings.get("watchlist_group"), set("ABCDEF"), "A"),
        "event_group": _choice(settings.get("event_group"), set("ABCDEF"), "A"),
        "macro_category": _choice(
            settings.get("macro_category"),
            {"rates", "inflation", "labor", "growth", "money", "credit"},
            "rates",
        ),
        "news_topic": _choice(
            settings.get("news_topic"),
            {query.label for query in default_news_queries()},
            "Markets",
        ),
        "search_sort": _choice(
            settings.get("search_sort"),
            {"Relevance", "Market Cap", "Exchange"},
            "Relevance",
        ),
        "chart_instruments": chart_instruments[:MAX_SERIES],
    }


def _choice(value: object, choices: set[str], default: str) -> str:
    return value if isinstance(value, str) and value in choices else default


def range_spec_state(range_spec: RangeSpec) -> dict:
    return {
        "label": range_spec.label,
        "period": range_spec.period,
        "interval": range_spec.interval,
        "start": range_spec.start,
        "end": range_spec.end,
    }


def range_spec_from_state(payload: object, chart_mode: str) -> RangeSpec:
    ranges = INTRADAY_RANGES if chart_mode == "Intraday" else HISTORICAL_RANGES
    default = ranges[0]
    if not isinstance(payload, dict):
        return default
    period = str(payload.get("period", "") or "")
    interval = str(payload.get("interval", "") or "")
    start = payload.get("start")
    end = payload.get("end")
    if period == "custom" and isinstance(start, str) and isinstance(end, str):
        try:
            return custom_range_spec(chart_mode, start, end, interval)
        except ValueError:
            return default
    for range_spec in ranges:
        if range_spec.period == period and range_spec.interval == interval:
            return range_spec
    label = str(payload.get("label", "") or "")
    for range_spec in ranges:
        if range_spec.label == label:
            return range_spec
    return default


def technical_study_state(study: tuple[str, int] | None) -> dict | None:
    if study is None:
        return None
    return {"name": study[0], "period": study[1]}


def technical_study_from_state(payload: object) -> tuple[str, int] | None:
    if payload in (None, "", {}, []):
        return None
    if not isinstance(payload, dict):
        return None
    name = str(payload.get("name", "") or "").upper()
    try:
        period = int(payload.get("period"))
    except (TypeError, ValueError):
        return None
    valid_periods = {
        ("RSI", 7),
        ("RSI", 14),
        ("RSI", 21),
        ("MOM", 5),
        ("MOM", 10),
        ("MOM", 20),
        ("SIGMA", 10),
        ("SIGMA", 20),
        ("SIGMA", 60),
    }
    return (name, period) if (name, period) in valid_periods else None


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


def window_layout_state(widget: tk.Widget) -> dict[str, int | bool]:
    state = window_place_geometry(widget)
    state["visible"] = widget.winfo_manager() == "place"
    return state


def save_watchlist_state(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            path.with_suffix(path.suffix + ".bak").write_text(
                path.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        except OSError:
            pass
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


def watchlist_group_row(name: str) -> dict:
    normalized = " ".join(name.strip().split())
    return {"type": "group", "name": normalized} if normalized else {}


def watchlist_group_name(row: dict) -> str:
    if row.get("type") != "group":
        return ""
    return " ".join(str(row.get("name", "")).strip().split())


def watchlist_group_label(name: str) -> str:
    return f"[ {name.upper()} ]"


def watchlist_display_values(row: dict, instrument: Instrument | None) -> tuple[str, ...]:
    if instrument is None:
        return ("", "", "", "", "", "", "")
    values = row.get("display_values")
    if isinstance(values, list):
        normalized = [str(value) for value in values[: len(WATCHLIST_COLUMNS)]]
        normalized.extend([""] * (len(WATCHLIST_COLUMNS) - len(normalized)))
        if normalized and normalized[0].strip():
            return tuple(normalized)
    return (watchlist_asset_label(instrument), "Loading", "", "", "", "", "")


def instrument_from_watchlist_row(row: dict) -> Instrument | None:
    if row.get("type") == "group":
        return None
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


def chart_header_summary_text(
    instrument: Instrument,
    frame: pd.DataFrame,
    last: float,
    change: float,
    change_percent: float,
    currency: str = "",
) -> str:
    parts = [instrument.symbol]
    if instrument.isin:
        parts.append(instrument.isin)
    parts.append(f"{last:,.2f}{currency}")
    parts.append(f"{change:+,.2f} ({change_percent:+.2f}%)")
    if "Volume" in frame.columns and not frame.empty:
        volume = pd.to_numeric(frame["Volume"], errors="coerce").dropna()
        if not volume.empty:
            parts.append(f"Vol {format_volume_value(float(volume.iloc[-1]))}")
    market_cap = format_billions(instrument.market_cap)
    if market_cap:
        parts.append(f"MKT {market_cap}")
    aum = format_billions(instrument.aum)
    if aum:
        parts.append(f"AUM {aum}")
    return "  |  ".join(parts)


def chart_live_quote_summary_text(instrument: Instrument, frame: pd.DataFrame, quote) -> str:
    last = getattr(quote, "last", None)
    if last is None:
        return ""
    currency = f" {instrument.currency}" if instrument.currency else ""
    change = getattr(quote, "change", None)
    change_percent = getattr(quote, "change_percent", None)
    parts = [instrument.symbol]
    if instrument.isin:
        parts.append(instrument.isin)
    parts.append(f"{last:,.2f}{currency}")
    if change is not None and change_percent is not None:
        parts.append(f"{change:+,.2f} ({change_percent:+.2f}%)")
    elif change_percent is not None:
        parts.append(f"{change_percent:+.2f}%")
    elif change is not None:
        parts.append(f"{change:+,.2f}")
    volume = getattr(quote, "volume", None)
    if volume is not None:
        parts.append(f"Vol {format_volume_value(volume)}")
    elif "Volume" in frame.columns and not frame.empty:
        history_volume = pd.to_numeric(frame["Volume"], errors="coerce").dropna()
        if not history_volume.empty:
            parts.append(f"Vol {format_volume_value(float(history_volume.iloc[-1]))}")
    source = quote_source_text(quote)
    if source:
        parts.append(source)
    return "  |  ".join(parts)


def quote_source_text(quote) -> str:
    source = str(getattr(quote, "source", "") or "").strip()
    if not source:
        return ""
    as_of = getattr(quote, "as_of", None)
    if as_of is None:
        return source
    timestamp = pd.Timestamp(as_of)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    local_timestamp = timestamp.tz_convert(datetime.now().astimezone().tzinfo)
    return f"{source} {local_timestamp:%H:%M:%S}"


def format_billions(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value / 1_000_000_000:,.1f}B"


def watchlist_asset_label(instrument: Instrument) -> str:
    return instrument.symbol


def format_quote_value(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:,.2f}"


def market_event_local_date_time(event: MarketEvent) -> tuple[str, str]:
    if event.timestamp is None:
        return "", "N/A"
    timestamp = pd.Timestamp(event.timestamp)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    local_timestamp = timestamp.tz_convert(datetime.now().astimezone().tzinfo)
    date_text = local_timestamp.strftime("%Y-%m-%d")
    time_text = "Date only" if event.is_date_only else local_timestamp.strftime("%H:%M")
    return date_text, time_text


def event_price_move_since_text(event: MarketEvent, frame: pd.DataFrame) -> str:
    if event.timestamp is None or frame.empty or "Close" not in frame:
        return ""
    closes = pd.to_numeric(frame["Close"], errors="coerce").dropna()
    if len(closes) < 2:
        return ""
    event_time = pd.Timestamp(event.timestamp)
    index = closes.index
    if getattr(index, "tz", None) is not None:
        if event_time.tzinfo is None:
            event_time = event_time.tz_localize(index.tz)
        else:
            event_time = event_time.tz_convert(index.tz)
    elif event_time.tzinfo is not None:
        event_time = event_time.tz_convert("UTC").tz_localize(None)
    if event.is_date_only:
        event_time = event_time.normalize()
    positions = index.searchsorted(event_time, side="left")
    if positions >= len(closes):
        return ""
    start = float(closes.iloc[int(positions)])
    latest = float(closes.iloc[-1])
    if start == 0:
        return ""
    percent = (latest - start) / start * 100
    return f"{percent:+.2f}%"


def market_event_display_sort_key(event: MarketEvent) -> tuple[int, pd.Timestamp, str]:
    if event.timestamp is None:
        return (1, pd.Timestamp.max, event.event)
    timestamp = pd.Timestamp(event.timestamp)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert("UTC").tz_localize(None)
    return (0, timestamp, event.event)


def market_event_is_past(event: MarketEvent, now: datetime | None = None) -> bool:
    if event.timestamp is None:
        return False
    current = pd.Timestamp(now or datetime.now(timezone.utc))
    timestamp = pd.Timestamp(event.timestamp)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    if current.tzinfo is None:
        current = current.tz_localize("UTC")
    return timestamp < current


def past_event_row_stripe(row_index: int) -> str:
    return "past_even" if row_index % 2 == 0 else "past_odd"


def format_bid_ask_value(value: float | None, quote, side: str) -> str:
    if value is not None:
        return format_quote_value(value)
    other = quote.ask if side == "bid" else quote.bid
    state = str(getattr(quote, "market_state", "") or "").upper()
    if state and state not in {"REGULAR", "OPEN"}:
        return "MKT CLOSED"
    if other is None and quote.last is not None:
        return "MKT CLOSED"
    return ""


def quote_allows_realtime_refresh(quote) -> bool:
    state = str(getattr(quote, "market_state", "") or "").upper().replace("_", " ")
    if not state:
        return True
    if "CLOSED" in state:
        return False
    refreshable_states = {"REGULAR", "OPEN", "PRE", "POST", "PRE MARKET", "POST MARKET"}
    return state in refreshable_states


def format_quote_change(change: float | None, change_percent: float | None = None) -> str:
    if change is None and change_percent is None:
        return ""
    if change_percent is not None:
        return f"{change_percent:+.2f}%"
    return ""


def quote_change_tag(change: float | None, change_percent: float | None = None) -> str:
    move = change if change is not None else change_percent
    if move is None:
        return "tick_flat"
    if move > 0:
        return "tick_up"
    if move < 0:
        return "tick_down"
    return "tick_flat"


def watchlist_row_stripe(row_index: int) -> str:
    return "watchlist_even" if row_index % 2 == 0 else "watchlist_odd"


def watchlist_item_tags(row_index: int, direction_tag: str) -> tuple[str, str]:
    return watchlist_row_stripe(row_index), direction_tag


def normalized_watchlist_column_widths(widths: object) -> dict[str, int]:
    defaults = {column: width for column, _title, width in WATCHLIST_COLUMNS}
    if not isinstance(widths, dict):
        return defaults
    normalized = defaults.copy()
    for column in defaults:
        try:
            width = int(widths[column])
        except (KeyError, TypeError, ValueError):
            continue
        if width >= WATCHLIST_MIN_COLUMN_WIDTH:
            normalized[column] = width
    return normalized


def beta_table_column_width(rows: list[tuple[str, ...]]) -> int:
    values = list(BETA_SERIES_HEADINGS.values())
    for row in rows:
        values.extend(str(value) for value in row)
    longest = max((len(value) for value in values), default=0)
    width = longest * 7 + 18
    return max(BETA_SERIES_MIN_COLUMN_WIDTH, min(width, BETA_SERIES_MAX_COLUMN_WIDTH))


def watchlist_heading_height(tree, fallback: int = 25) -> int:
    items = tree.get_children()
    if not items:
        return fallback
    first_row_box = tree.bbox(items[0])
    if not first_row_box:
        return fallback
    return int(first_row_box[1])


def price_move_color(frame: pd.DataFrame | None, fallback: str) -> str:
    if frame is None or "Close" not in frame:
        return fallback
    closes = pd.to_numeric(frame["Close"], errors="coerce").dropna()
    if len(closes) < 2:
        return fallback
    change = float(closes.iloc[-1] - closes.iloc[-2])
    if change > 0:
        return UP
    if change < 0:
        return DOWN
    return fallback


def format_signed_value(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:+,.2f}"


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


def comparison_y_axis_label(symbols: list[str], display_mode: str) -> str:
    prefix = "Indexed (100)" if display_mode == "Rebased 100" else "Price"
    if len(symbols) < 2:
        return prefix
    return f"{prefix}: {', '.join(symbols)}"


def comparison_latest_value_label(symbol: str, value: float) -> str:
    return f"{symbol} {format_quote_value(value)}"


def comparison_series_color(
    frame: pd.DataFrame | None, position: int, series_count: int
) -> str:
    fallback = SERIES_COLORS[position % len(SERIES_COLORS)]
    if series_count > 1:
        return fallback
    return price_move_color(frame, fallback)


def comparison_series_colors(
    instruments: list[Instrument], frames: dict[str, pd.DataFrame]
) -> dict[str, str]:
    series_count = len(instruments)
    return {
        instrument.symbol: comparison_series_color(
            frames.get(instrument.symbol),
            position,
            series_count,
        )
        for position, instrument in enumerate(instruments)
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


def format_latency_value(value_ms: float | None) -> str:
    if value_ms is None:
        return ""
    if value_ms >= 1000:
        return f"{value_ms / 1000:.2f}s"
    return f"{value_ms:.0f}ms"


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
