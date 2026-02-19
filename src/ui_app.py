"""
Trade Copier — GUI (CustomTkinter)
Sleek dark UI with cyan + rose-gold accents.
"""

import queue
import threading
import logging
import tkinter as tk
from datetime import datetime
from typing import List, Optional, Dict

import customtkinter as ctk

from src.models import AccountConfig
from src.config import ConfigError
from src.logger import setup_logger

# ── Appearance ────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

# ── Palette ───────────────────────────────────────────────────────────────────
C = {
    "bg_deep":    "#0D1117",
    "bg_card":    "#161B22",
    "bg_input":   "#21262D",
    "bg_hover":   "#1C2128",
    "border":     "#30363D",
    "cyan":       "#00D4CF",
    "cyan_dim":   "#009E9A",
    "cyan_faint": "#003D3B",
    "rose":       "#C4878A",
    "rose_dim":   "#A06A6D",
    "rose_faint": "#3D2224",
    "green":      "#3FB950",
    "red":        "#F85149",
    "yellow":     "#D29922",
    "text":       "#E6EDF3",
    "muted":      "#8B949E",
    "muted2":     "#484F58",
}

FONT_FAMILY = "Segoe UI"


# ── Log queue handler (thread → UI) ──────────────────────────────────────────
class QueueHandler(logging.Handler):
    """Puts formatted log records into a queue to be consumed by the UI thread."""

    def __init__(self, log_queue: queue.Queue):
        super().__init__()
        self._queue = log_queue

    def emit(self, record: logging.LogRecord):
        msg = record.getMessage()
        level = record.levelname
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        self._queue.put_nowait((ts, level, msg))


# ── Account card widget ───────────────────────────────────────────────────────
class AccountCard(ctk.CTkFrame):
    STATUS_COLORS = {
        "idle":        C["muted2"],
        "checking":    C["yellow"],
        "connected":   C["green"],
        "error":       C["red"],
        "disabled":    C["muted2"],
    }

    def __init__(self, parent, config: AccountConfig, is_master: bool = False, **kwargs):
        super().__init__(
            parent,
            fg_color=C["bg_card"],
            corner_radius=10,
            border_width=1,
            border_color=C["border"],
            **kwargs,
        )
        self.config = config
        self._status = "disabled" if not config.enabled else "idle"

        # Role badge + status dot row
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(12, 0))

        role_color = C["cyan"] if is_master else C["rose"]
        role_label = "MASTER" if is_master else "SLAVE"
        ctk.CTkLabel(
            top,
            text=role_label,
            font=(FONT_FAMILY, 9, "bold"),
            text_color=role_color,
            fg_color=C["bg_input"],
            corner_radius=4,
            width=56,
            height=20,
        ).pack(side="left")

        # Status dot (canvas circle)
        self._dot_canvas = tk.Canvas(
            top, width=10, height=10,
            bg=C["bg_card"], highlightthickness=0,
        )
        self._dot_canvas.pack(side="right", padx=(4, 0))
        self._dot = self._dot_canvas.create_oval(1, 1, 9, 9, fill=C["muted2"], outline="")

        self._status_label = ctk.CTkLabel(
            top,
            text="Disabled" if not config.enabled else "Idle",
            font=(FONT_FAMILY, 10),
            text_color=C["muted"],
        )
        self._status_label.pack(side="right", padx=(0, 6))

        # Account name
        ctk.CTkLabel(
            self,
            text=config.name,
            font=(FONT_FAMILY, 13, "bold"),
            text_color=C["text"],
            anchor="w",
        ).pack(fill="x", padx=14, pady=(6, 0))

        # Login + server
        ctk.CTkLabel(
            self,
            text=f"#{config.login}  ·  {config.server}",
            font=(FONT_FAMILY, 10),
            text_color=C["muted"],
            anchor="w",
        ).pack(fill="x", padx=14)

        # Lot badge (slaves only)
        if not is_master:
            lot_text = (
                f"Fixed {config.lot_value} lot"
                if config.lot_mode.value == "fixed"
                else f"{config.lot_value}× multiplier"
            )
            ctk.CTkLabel(
                self,
                text=lot_text,
                font=(FONT_FAMILY, 10),
                text_color=C["rose"],
                fg_color=C["rose_faint"],
                corner_radius=4,
                anchor="w",
                width=1,
            ).pack(anchor="w", padx=14, pady=(4, 0))

        # Balance row (hidden until connected)
        self._balance_label = ctk.CTkLabel(
            self,
            text="",
            font=(FONT_FAMILY, 11, "bold"),
            text_color=C["cyan"],
            anchor="w",
        )
        self._balance_label.pack(fill="x", padx=14, pady=(4, 12))

    def set_status(self, status: str, balance: Optional[float] = None, currency: str = ""):
        self._status = status
        color = self.STATUS_COLORS.get(status, C["muted2"])
        self._dot_canvas.itemconfig(self._dot, fill=color)

        label_map = {
            "idle":      ("Idle",       C["muted"]),
            "checking":  ("Checking…",  C["yellow"]),
            "connected": ("Connected",  C["green"]),
            "error":     ("Error",      C["red"]),
            "disabled":  ("Disabled",   C["muted2"]),
        }
        text, text_color = label_map.get(status, ("Unknown", C["muted"]))
        self._status_label.configure(text=text, text_color=text_color)

        if balance is not None and status == "connected":
            self._balance_label.configure(
                text=f"{balance:,.2f} {currency}"
            )
        elif status in ("idle", "checking", "error"):
            self._balance_label.configure(text="")


# ── Stats bar item ────────────────────────────────────────────────────────────
class StatItem(ctk.CTkFrame):
    def __init__(self, parent, label: str, color: str, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)
        self._val_label = ctk.CTkLabel(
            self, text="0",
            font=(FONT_FAMILY, 22, "bold"),
            text_color=color,
        )
        self._val_label.pack()
        ctk.CTkLabel(
            self, text=label,
            font=(FONT_FAMILY, 10),
            text_color=C["muted"],
        ).pack()

    def set_value(self, value: int):
        self._val_label.configure(text=str(value))


# ── Main application window ───────────────────────────────────────────────────
class TradeCopierApp(ctk.CTk):

    def __init__(
        self,
        master_config: AccountConfig,
        slave_configs: List[AccountConfig],
        settings: dict,
        config_path: str = "",
    ):
        super().__init__(fg_color=C["bg_deep"])

        self.master_cfg   = master_config
        self.slave_cfgs   = slave_configs
        self.settings     = settings
        self.config_path  = config_path

        self._copier_thread: Optional[threading.Thread] = None
        self._copier_instance = None
        self._running = False

        # Thread-safe log queue
        self._log_queue: queue.Queue = queue.Queue()
        self._event_queue: queue.Queue = queue.Queue()  # account status events

        # Wire logger → queue
        self._setup_logging()

        # Window chrome
        self.title("Trade Copier")
        self.geometry("1080x680")
        self.minsize(860, 560)
        self.configure(fg_color=C["bg_deep"])

        # Prevent resize handles from showing gap
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Build UI
        self._build_layout()
        self._start_polling()

    # ── Logging bridge ────────────────────────────────────────────────────────

    def _setup_logging(self):
        logger = setup_logger(level=self.settings.get("log_level", "INFO"))
        handler = QueueHandler(self._log_queue)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build_layout(self):
        root = ctk.CTkFrame(self, fg_color=C["bg_deep"], corner_radius=0)
        root.pack(fill="both", expand=True)
        root.grid_columnconfigure(1, weight=1)
        root.grid_rowconfigure(1, weight=1)

        self._build_header(root)
        self._build_sidebar(root)
        self._build_log_panel(root)
        self._build_stats_bar(root)

    # ── Header ────────────────────────────────────────────────────────────────

    def _build_header(self, parent):
        header = ctk.CTkFrame(
            parent, fg_color=C["bg_card"],
            corner_radius=0, height=60,
            border_width=0,
        )
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        header.grid_columnconfigure(1, weight=1)
        header.grid_propagate(False)

        # Logo dot + title
        logo_frame = ctk.CTkFrame(header, fg_color="transparent")
        logo_frame.pack(side="left", padx=20)

        dot = tk.Canvas(logo_frame, width=10, height=10,
                        bg=C["bg_card"], highlightthickness=0)
        dot.pack(side="left", pady=24, padx=(0, 8))
        dot.create_oval(0, 0, 10, 10, fill=C["cyan"], outline="")

        ctk.CTkLabel(
            logo_frame,
            text="Trade Copier",
            font=(FONT_FAMILY, 17, "bold"),
            text_color=C["text"],
        ).pack(side="left")

        ctk.CTkLabel(
            logo_frame,
            text="MT5",
            font=(FONT_FAMILY, 10, "bold"),
            text_color=C["cyan"],
            fg_color=C["cyan_faint"],
            corner_radius=4,
            width=30,
            height=18,
        ).pack(side="left", padx=(10, 0), pady=21)

        # Right side: accounts button + status pill + start/stop
        right = ctk.CTkFrame(header, fg_color="transparent")
        right.pack(side="right", padx=20)

        ctk.CTkButton(
            right,
            text="⚙  Accounts",
            width=110,
            height=36,
            font=(FONT_FAMILY, 12),
            fg_color=C["bg_input"],
            hover_color=C["bg_hover"],
            text_color=C["muted"],
            border_width=1,
            border_color=C["border"],
            corner_radius=8,
            command=self._open_accounts_modal,
        ).pack(side="left", padx=(0, 10))

        self._status_pill = ctk.CTkLabel(
            right,
            text="  ●  STOPPED  ",
            font=(FONT_FAMILY, 11, "bold"),
            text_color=C["muted"],
            fg_color=C["bg_input"],
            corner_radius=20,
            height=30,
        )
        self._status_pill.pack(side="left", padx=(0, 14))

        self._toggle_btn = ctk.CTkButton(
            right,
            text="▶  Start",
            width=110,
            height=36,
            font=(FONT_FAMILY, 12, "bold"),
            fg_color=C["cyan_dim"],
            hover_color=C["cyan"],
            text_color="#000000",
            corner_radius=8,
            command=self._toggle_copier,
        )
        self._toggle_btn.pack(side="left")

    # ── Sidebar (account cards) ───────────────────────────────────────────────

    def _build_sidebar(self, parent):
        sidebar = ctk.CTkFrame(
            parent, fg_color=C["bg_deep"],
            corner_radius=0, width=240,
            border_width=0,
        )
        sidebar.grid(row=1, column=0, sticky="nsew", padx=(12, 0), pady=12)
        sidebar.grid_propagate(False)

        ctk.CTkLabel(
            sidebar,
            text="ACCOUNTS",
            font=(FONT_FAMILY, 10, "bold"),
            text_color=C["muted"],
            anchor="w",
        ).pack(fill="x", padx=8, pady=(4, 8))

        self._list_scroll = ctk.CTkScrollableFrame(
            sidebar,
            fg_color="transparent",
            scrollbar_button_color=C["border"],
            scrollbar_button_hover_color=C["muted2"],
        )
        self._list_scroll.pack(fill="both", expand=True)

        self._account_cards: Dict[str, AccountCard] = {}

        # Master card
        card = AccountCard(self._list_scroll, self.master_cfg, is_master=True)
        card.pack(fill="x", pady=(0, 8))
        self._account_cards[str(self.master_cfg.login)] = card

        # Slave cards
        for sc in self.slave_cfgs:
            card = AccountCard(self._list_scroll, sc, is_master=False)
            card.pack(fill="x", pady=(0, 8))
            self._account_cards[str(sc.login)] = card

    # ── Log panel ─────────────────────────────────────────────────────────────

    def _build_log_panel(self, parent):
        panel = ctk.CTkFrame(
            parent, fg_color=C["bg_deep"], corner_radius=0,
        )
        panel.grid(row=1, column=1, sticky="nsew", padx=12, pady=12)
        panel.grid_rowconfigure(1, weight=1)
        panel.grid_columnconfigure(0, weight=1)

        # Log header row
        log_header = ctk.CTkFrame(panel, fg_color="transparent")
        log_header.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        ctk.CTkLabel(
            log_header,
            text="ACTIVITY LOG",
            font=(FONT_FAMILY, 10, "bold"),
            text_color=C["muted"],
        ).pack(side="left")

        ctk.CTkButton(
            log_header,
            text="Clear",
            width=56,
            height=24,
            font=(FONT_FAMILY, 10),
            fg_color=C["bg_input"],
            hover_color=C["bg_hover"],
            text_color=C["muted"],
            corner_radius=6,
            command=self._clear_log,
        ).pack(side="right")

        # Log text widget (tk.Text for tag-based coloring)
        log_frame = ctk.CTkFrame(
            panel,
            fg_color=C["bg_card"],
            corner_radius=10,
            border_width=1,
            border_color=C["border"],
        )
        log_frame.grid(row=1, column=0, sticky="nsew")
        log_frame.grid_rowconfigure(0, weight=1)
        log_frame.grid_columnconfigure(0, weight=1)

        self._log_text = tk.Text(
            log_frame,
            bg=C["bg_card"],
            fg=C["text"],
            insertbackground=C["cyan"],
            selectbackground=C["cyan_faint"],
            selectforeground=C["text"],
            font=(FONT_FAMILY, 11),
            relief="flat",
            bd=0,
            padx=14,
            pady=10,
            wrap="word",
            state="disabled",
            cursor="arrow",
        )
        self._log_text.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)

        scrollbar = ctk.CTkScrollbar(
            log_frame,
            command=self._log_text.yview,
            button_color=C["border"],
            button_hover_color=C["muted2"],
            fg_color=C["bg_card"],
        )
        scrollbar.grid(row=0, column=1, sticky="ns", pady=1, padx=(0, 1))
        self._log_text.configure(yscrollcommand=scrollbar.set)

        # Configure text tags
        self._log_text.tag_configure("ts",       foreground=C["muted"],   font=(FONT_FAMILY, 10))
        self._log_text.tag_configure("open",     foreground=C["cyan"],    font=(FONT_FAMILY, 11, "bold"))
        self._log_text.tag_configure("close",    foreground=C["rose"],    font=(FONT_FAMILY, 11, "bold"))
        self._log_text.tag_configure("modify",   foreground=C["yellow"],  font=(FONT_FAMILY, 11, "bold"))
        self._log_text.tag_configure("success",  foreground=C["green"],   font=(FONT_FAMILY, 11))
        self._log_text.tag_configure("failed",   foreground=C["red"],     font=(FONT_FAMILY, 11))
        self._log_text.tag_configure("skipped",  foreground=C["muted"],   font=(FONT_FAMILY, 11))
        self._log_text.tag_configure("info",     foreground=C["text"],    font=(FONT_FAMILY, 11))
        self._log_text.tag_configure("error",    foreground=C["red"],     font=(FONT_FAMILY, 11))
        self._log_text.tag_configure("warning",  foreground=C["yellow"],  font=(FONT_FAMILY, 11))
        self._log_text.tag_configure("master",   foreground=C["cyan_dim"],font=(FONT_FAMILY, 10, "bold"))
        self._log_text.tag_configure("arrow",    foreground=C["muted"],   font=(FONT_FAMILY, 11))
        self._log_text.tag_configure("slave_ok", foreground=C["green"],   font=(FONT_FAMILY, 11))
        self._log_text.tag_configure("slave_fail",foreground=C["red"],    font=(FONT_FAMILY, 11))

        # Welcome message
        self._append_log_entry(
            datetime.now().strftime("%H:%M:%S"),
            "info",
            "Trade Copier ready. Fill in config/accounts.json then press ▶ Start.",
        )

    # ── Stats bar ─────────────────────────────────────────────────────────────

    def _build_stats_bar(self, parent):
        bar = ctk.CTkFrame(
            parent,
            fg_color=C["bg_card"],
            corner_radius=0,
            height=72,
            border_width=0,
        )
        bar.grid(row=2, column=0, columnspan=2, sticky="ew")
        bar.grid_propagate(False)
        bar.grid_columnconfigure((0, 1, 2, 3, 4), weight=1)

        divider_color = C["border"]

        self._stat_opens    = StatItem(bar, "OPENS",    C["cyan"])
        self._stat_closes   = StatItem(bar, "CLOSES",   C["rose"])
        self._stat_modifies = StatItem(bar, "MODIFIES", C["yellow"])
        self._stat_errors   = StatItem(bar, "ERRORS",   C["red"])

        self._stat_opens.grid   (row=0, column=0, pady=10)
        self._stat_closes.grid  (row=0, column=1, pady=10)
        self._stat_modifies.grid(row=0, column=2, pady=10)
        self._stat_errors.grid  (row=0, column=3, pady=10)

        # Poll interval info
        poll_ms = self.settings.get("poll_interval_ms", 500)
        ctk.CTkLabel(
            bar,
            text=f"Poll\n{poll_ms}ms",
            font=(FONT_FAMILY, 10),
            text_color=C["muted"],
        ).grid(row=0, column=4, pady=10)

    # ── Copier control ────────────────────────────────────────────────────────

    def _toggle_copier(self):
        if self._running:
            self._stop_copier()
        else:
            self._start_copier()

    def _start_copier(self):
        # Avoid double-start
        if self._running:
            return

        self._running = True
        self._toggle_btn.configure(
            text="■  Stop",
            fg_color=C["rose_dim"],
            hover_color=C["rose"],
        )
        self._status_pill.configure(
            text="  ●  RUNNING  ",
            text_color=C["green"],
            fg_color=C["bg_input"],
        )

        # Mark all enabled accounts as "checking"
        self._set_all_cards_status("checking")

        # Import here to avoid issues if MT5 not installed
        try:
            from src.copier import TradeCopier
        except ImportError as e:
            self._append_log_entry(
                datetime.now().strftime("%H:%M:%S"), "error",
                f"Import error: {e}"
            )
            self._stop_copier()
            return

        self._copier_instance = TradeCopier(
            master_config=self.master_cfg,
            slave_configs=self.slave_cfgs,
            settings=self.settings,
        )

        self._copier_thread = threading.Thread(
            target=self._run_copier_thread,
            daemon=True,
            name="CopierThread",
        )
        self._copier_thread.start()

    def _run_copier_thread(self):
        try:
            self._copier_instance.start()
        except Exception as e:
            from src.logger import get_logger
            get_logger().error(f"Copier thread error: {e}")
        finally:
            self._running = False
            # Schedule UI update on main thread
            self.after(0, self._on_copier_stopped)

    def _stop_copier(self):
        if self._copier_instance:
            self._copier_instance.stop()
        self._running = False
        self._on_copier_stopped()

    def _on_copier_stopped(self):
        self._running = False
        self._toggle_btn.configure(
            text="▶  Start",
            fg_color=C["cyan_dim"],
            hover_color=C["cyan"],
            text_color="#000000",
        )
        self._status_pill.configure(
            text="  ●  STOPPED  ",
            text_color=C["muted"],
        )
        self._set_all_cards_status("idle")

    def _set_all_cards_status(self, status: str):
        for card in self._account_cards.values():
            if card.config.enabled or status == "idle":
                card.set_status(status)

    # ── Polling (UI thread timers) ────────────────────────────────────────────

    def _start_polling(self):
        self._poll_log_queue()
        self._poll_stats()
        self._poll_connections()

    def _poll_log_queue(self):
        """Drain log queue and write entries to the text widget."""
        try:
            while True:
                ts, level, msg = self._log_queue.get_nowait()
                self._route_log_entry(ts, level, msg)
        except queue.Empty:
            pass
        self.after(80, self._poll_log_queue)

    def _poll_stats(self):
        """Refresh the stats bar from the running copier."""
        if self._copier_instance and self._running:
            self._stat_opens.set_value(self._copier_instance.total_opens)
            self._stat_closes.set_value(self._copier_instance.total_closes)
            self._stat_modifies.set_value(self._copier_instance.total_modifies)
            self._stat_errors.set_value(self._copier_instance.total_errors)
        self.after(500, self._poll_stats)

    def _poll_connections(self):
        """
        While the copier is running, probe each account periodically
        and update their cards.
        """
        if self._running and self._copier_instance:
            threading.Thread(
                target=self._check_connections_bg,
                daemon=True,
            ).start()
        self.after(5000, self._poll_connections)

    def _check_connections_bg(self):
        """Background thread: connect to each account and emit status events."""
        from src.mt5_connector import MT5Connector
        try:
            import MetaTrader5 as mt5
        except ImportError:
            return

        for cfg in [self.master_cfg] + [s for s in self.slave_cfgs if s.enabled]:
            conn = MT5Connector(cfg)
            try:
                ok = conn.connect(timeout_ms=4000)
                if ok:
                    info = mt5.account_info()
                    balance = info.balance if info else None
                    currency = info.currency if info else ""
                    self.after(0, lambda c=cfg, b=balance, cu=currency: self._update_card(
                        str(c.login), "connected", b, cu
                    ))
                else:
                    self.after(0, lambda c=cfg: self._update_card(str(c.login), "error"))
            except Exception:
                self.after(0, lambda c=cfg: self._update_card(str(c.login), "error"))
            finally:
                conn.shutdown()

    def _update_card(self, login_key: str, status: str,
                     balance: Optional[float] = None, currency: str = ""):
        card = self._account_cards.get(login_key)
        if card:
            card.set_status(status, balance, currency)

    # ── Log helpers ───────────────────────────────────────────────────────────

    def _route_log_entry(self, ts: str, level: str, msg: str):
        """Choose the right color tags based on message content."""
        msg_upper = msg.upper()

        if "[MASTER]" in msg_upper:
            if " OPEN " in msg_upper or msg_upper.strip().endswith("OPEN"):
                tag = "open"
            elif " CLOSE" in msg_upper:
                tag = "close"
            elif " MODIFY" in msg_upper:
                tag = "modify"
            else:
                tag = "master"
        elif "→" in msg or msg.strip().startswith("→"):
            if "SUCCESS" in msg_upper:
                tag = "slave_ok"
            elif "FAILED" in msg_upper:
                tag = "slave_fail"
            elif "SKIPPED" in msg_upper:
                tag = "skipped"
            else:
                tag = "arrow"
        elif level == "ERROR":
            tag = "error"
        elif level == "WARNING":
            tag = "warning"
        else:
            tag = "info"

        self._append_log_entry(ts, tag, msg)

    def _append_log_entry(self, ts: str, tag: str, msg: str):
        """Append a single line to the log text widget."""
        self._log_text.configure(state="normal")
        # Check auto-scroll (are we at the bottom?)
        at_bottom = self._log_text.yview()[1] >= 0.98

        self._log_text.insert("end", f"{ts}  ", "ts")
        self._log_text.insert("end", msg + "\n", tag)

        if at_bottom:
            self._log_text.see("end")

        self._log_text.configure(state="disabled")

    def _clear_log(self):
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.configure(state="disabled")

    # ── Accounts modal ───────────────────────────────────────────────────────

    def _open_accounts_modal(self):
        from src.ui_accounts import AccountsModal
        AccountsModal(
            parent=self,
            config_path=self.config_path,
            on_save=self._on_accounts_saved,
        )

    def _on_accounts_saved(self, master_raw: dict, slaves_raw: list, settings: dict):
        """Called by the modal after the user saves. Reloads configs and rebuilds UI."""
        from src.config import load_config, ConfigError

        was_running = self._running
        if was_running:
            self._stop_copier()

        try:
            new_master, new_slaves, new_settings = load_config(self.config_path)
        except ConfigError as e:
            self._append_log_entry(
                datetime.now().strftime("%H:%M:%S"),
                "error", f"Config reload error: {e}",
            )
            return

        self.master_cfg = new_master
        self.slave_cfgs = new_slaves
        self.settings   = new_settings

        self._rebuild_account_cards()
        self._append_log_entry(
            datetime.now().strftime("%H:%M:%S"),
            "info", "Accounts updated and saved.",
        )

    def _rebuild_account_cards(self):
        """Destroy and recreate all account cards in the sidebar."""
        # Find the scrollable list frame and clear it
        for widget in self._list_scroll.winfo_children():
            widget.destroy()
        self._account_cards.clear()

        # Rebuild master card
        card = AccountCard(self._list_scroll, self.master_cfg, is_master=True)
        card.pack(fill="x", pady=(0, 8))
        self._account_cards[str(self.master_cfg.login)] = card

        # Rebuild slave cards
        for sc in [s for s in self.slave_cfgs if s.enabled]:
            card = AccountCard(self._list_scroll, sc, is_master=False)
            card.pack(fill="x", pady=(0, 8))
            self._account_cards[str(sc.login)] = card

    # ── Clean shutdown ────────────────────────────────────────────────────────

    def on_close(self):
        if self._running:
            self._stop_copier()
        self.destroy()
