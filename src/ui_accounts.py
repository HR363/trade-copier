"""
Accounts Manager Modal — add, edit, and delete MT5 accounts from the UI.
Changes are saved directly back to accounts.json.
"""

import json
import os
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Callable, Optional, List, Dict, Any
import threading

import customtkinter as ctk

# Reuse the palette from ui_app
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
FF = "Segoe UI"


# ── Small helper widgets ──────────────────────────────────────────────────────

def _section_label(parent, text: str):
    ctk.CTkLabel(
        parent, text=text,
        font=(FF, 9, "bold"),
        text_color=C["muted"],
        anchor="w",
    ).pack(fill="x", pady=(12, 2))


def _field(parent, label: str, var: tk.StringVar,
           placeholder: str = "", show: str = "") -> ctk.CTkEntry:
    _section_label(parent, label)
    e = ctk.CTkEntry(
        parent,
        textvariable=var,
        placeholder_text=placeholder,
        show=show,
        fg_color=C["bg_input"],
        border_color=C["border"],
        text_color=C["text"],
        placeholder_text_color=C["muted2"],
        font=(FF, 12),
        height=34,
        corner_radius=6,
    )
    e.pack(fill="x")
    return e


def _divider(parent):
    ctk.CTkFrame(parent, height=1, fg_color=C["border"]).pack(fill="x", pady=(14, 0))


# ── Account list row ──────────────────────────────────────────────────────────

class AccountRow(ctk.CTkFrame):
    def __init__(self, parent, account: dict, is_master: bool,
                 on_select: Callable, **kwargs):
        super().__init__(
            parent,
            fg_color=C["bg_card"],
            corner_radius=8,
            border_width=1,
            border_color=C["border"],
            cursor="hand2",
            **kwargs,
        )
        self._on_select = on_select
        self._account = account
        self._is_master = is_master
        self._selected = False

        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.pack(fill="x", padx=10, pady=7)

        # Role badge
        role_color = C["cyan"] if is_master else C["rose"]
        ctk.CTkLabel(
            inner,
            text="MASTER" if is_master else "SLAVE",
            font=(FF, 8, "bold"),
            text_color=role_color,
            fg_color=C["bg_deep"],
            corner_radius=3,
            width=44,
            height=17,
        ).pack(side="left", padx=(0, 8))

        # Name + login
        info = ctk.CTkFrame(inner, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True)

        name = account.get("name", f"Account #{account.get('login','?')}")
        ctk.CTkLabel(
            info, text=name,
            font=(FF, 12, "bold"),
            text_color=C["text"],
            anchor="w",
        ).pack(fill="x")
        ctk.CTkLabel(
            info,
            text=f"#{account.get('login','')}  ·  {account.get('server','')}",
            font=(FF, 10),
            text_color=C["muted"],
            anchor="w",
        ).pack(fill="x")

        # Enabled dot
        dot_color = C["green"] if account.get("enabled", True) else C["muted2"]
        dot_cv = tk.Canvas(inner, width=8, height=8,
                           bg=C["bg_card"], highlightthickness=0)
        dot_cv.pack(side="right")
        dot_cv.create_oval(0, 0, 8, 8, fill=dot_color, outline="")

        # Click anywhere on the row to select
        self.bind("<Button-1>", self._clicked)
        for child in self.winfo_children():
            child.bind("<Button-1>", self._clicked)
            for gc in child.winfo_children():
                gc.bind("<Button-1>", self._clicked)

    def _clicked(self, _event=None):
        self._on_select(self._account, self._is_master)

    def set_selected(self, selected: bool):
        self._selected = selected
        color = C["cyan_faint"] if selected else C["bg_card"]
        border = C["cyan"] if selected else C["border"]
        self.configure(fg_color=color, border_color=border)


# ── Main modal ────────────────────────────────────────────────────────────────

class AccountsModal(ctk.CTkToplevel):
    """
    Modal window for managing master & slave accounts.
    Pass an `on_save` callback — it receives (master_raw, slaves_raw, settings)
    after the user saves.
    """

    def __init__(
        self,
        parent,
        config_path: str,
        on_save: Callable[[dict, list, dict], None],
    ):
        super().__init__(parent)
        self.config_path = config_path
        self.on_save = on_save

        self.title("Manage Accounts")
        self.geometry("900x620")
        self.minsize(780, 500)
        self.configure(fg_color=C["bg_deep"])
        self.resizable(True, True)
        self.grab_set()  # make modal

        # Load current config
        self._raw: dict = self._load_raw()
        self._master_raw: dict = dict(self._raw.get("master", {}))
        self._slaves_raw: List[dict] = [dict(s) for s in self._raw.get("slaves", [])]
        self._settings: dict = dict(self._raw.get("settings", {}))

        # Currently edited account
        self._current_account: Optional[dict] = None
        self._current_is_master: bool = False
        self._rows: List[AccountRow] = []

        # Form variables
        self._v_name      = tk.StringVar()
        self._v_login     = tk.StringVar()
        self._v_password  = tk.StringVar()
        self._v_server    = tk.StringVar()
        self._v_path      = tk.StringVar()
        self._v_lot_mode  = tk.StringVar(value="multiplier")
        self._v_lot_value = tk.StringVar(value="1.0")
        self._v_enabled   = tk.BooleanVar(value=True)

        self._build_ui()

        # Auto-select master on open
        if self._master_raw:
            self._select_account(self._master_raw, is_master=True)

    # ── Raw config I/O ────────────────────────────────────────────────────────

    def _load_raw(self) -> dict:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path) as f:
                    return json.load(f)
            except Exception:
                pass
        return {"master": {}, "slaves": [], "settings": {}}

    def _write_raw(self):
        data = {
            "master":   self._master_raw,
            "slaves":   self._slaves_raw,
            "settings": self._settings,
        }
        with open(self.config_path, "w") as f:
            json.dump(data, f, indent=2)

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Title bar row
        header = ctk.CTkFrame(self, fg_color=C["bg_card"], corner_radius=0, height=52)
        header.pack(fill="x")
        header.pack_propagate(False)

        ctk.CTkLabel(
            header, text="Manage Accounts",
            font=(FF, 15, "bold"), text_color=C["text"],
        ).pack(side="left", padx=20, pady=14)

        # Save All button
        self._save_btn = ctk.CTkButton(
            header,
            text="💾  Save All",
            width=120, height=32,
            font=(FF, 12, "bold"),
            fg_color=C["cyan_dim"],
            hover_color=C["cyan"],
            text_color="#000000",
            corner_radius=7,
            command=self._save_all,
        )
        self._save_btn.pack(side="right", padx=16, pady=10)

        # Status label (shows "Saved!" etc.)
        self._status_lbl = ctk.CTkLabel(
            header, text="",
            font=(FF, 11),
            text_color=C["green"],
        )
        self._status_lbl.pack(side="right", padx=(0, 8))

        # Body: list (left) + form (right)
        body = ctk.CTkFrame(self, fg_color=C["bg_deep"])
        body.pack(fill="both", expand=True, padx=14, pady=14)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        self._build_list_panel(body)
        self._build_form_panel(body)

    # ── Left — account list ───────────────────────────────────────────────────

    def _build_list_panel(self, parent):
        pane = ctk.CTkFrame(parent, fg_color=C["bg_deep"], width=260)
        pane.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        pane.grid_propagate(False)
        pane.grid_rowconfigure(1, weight=1)
        pane.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            pane, text="ACCOUNTS",
            font=(FF, 9, "bold"),
            text_color=C["muted"], anchor="w",
        ).grid(row=0, column=0, sticky="w", pady=(0, 6))

        self._list_scroll = ctk.CTkScrollableFrame(
            pane, fg_color="transparent",
            scrollbar_button_color=C["border"],
        )
        self._list_scroll.grid(row=1, column=0, sticky="nsew")

        # Add slave button
        ctk.CTkButton(
            pane,
            text="＋  Add Slave Account",
            height=34, font=(FF, 11),
            fg_color=C["bg_card"],
            hover_color=C["bg_hover"],
            border_width=1,
            border_color=C["border"],
            text_color=C["rose"],
            corner_radius=7,
            command=self._add_slave,
        ).grid(row=2, column=0, sticky="ew", pady=(10, 0))

        self._refresh_list()

    def _refresh_list(self):
        # Clear existing rows
        for w in self._list_scroll.winfo_children():
            w.destroy()
        self._rows.clear()

        # Master row
        if self._master_raw:
            row = AccountRow(
                self._list_scroll, self._master_raw, is_master=True,
                on_select=self._select_account,
            )
            row.pack(fill="x", pady=(0, 6))
            self._rows.append(row)

        # Slave rows
        for s in self._slaves_raw:
            row = AccountRow(
                self._list_scroll, s, is_master=False,
                on_select=self._select_account,
            )
            row.pack(fill="x", pady=(0, 6))
            self._rows.append(row)

        # Re-highlight selected
        if self._current_account is not None:
            for r in self._rows:
                is_same = (r._account is self._current_account)
                r.set_selected(is_same)

    # ── Right — edit form ─────────────────────────────────────────────────────

    def _build_form_panel(self, parent):
        outer = ctk.CTkFrame(
            parent, fg_color=C["bg_card"],
            corner_radius=10,
            border_width=1,
            border_color=C["border"],
        )
        outer.grid(row=0, column=1, sticky="nsew")
        outer.grid_rowconfigure(0, weight=1)
        outer.grid_columnconfigure(0, weight=1)

        self._form_scroll = ctk.CTkScrollableFrame(
            outer, fg_color="transparent",
            scrollbar_button_color=C["border"],
        )
        self._form_scroll.grid(row=0, column=0, sticky="nsew", padx=20, pady=10)

        self._no_selection_lbl = ctk.CTkLabel(
            self._form_scroll,
            text="← Select an account to edit",
            font=(FF, 13),
            text_color=C["muted"],
        )
        self._no_selection_lbl.pack(pady=40)

        # Pre-build form fields (hidden until account selected)
        self._form_container = ctk.CTkFrame(
            self._form_scroll, fg_color="transparent"
        )

        # Account type badge
        self._form_role_lbl = ctk.CTkLabel(
            self._form_container,
            text="",
            font=(FF, 9, "bold"),
            text_color=C["cyan"],
            fg_color=C["bg_input"],
            corner_radius=4,
            width=56, height=20,
            anchor="center",
        )
        self._form_role_lbl.pack(anchor="w", pady=(4, 0))

        # ── Credentials section ──────────────────────────────────────────────
        _section_label(self._form_container, "DISPLAY NAME")
        _field(self._form_container, "", self._v_name, "e.g. My ICMarkets Account")

        cols = ctk.CTkFrame(self._form_container, fg_color="transparent")
        cols.pack(fill="x", pady=(0, 0))
        cols.grid_columnconfigure((0, 1), weight=1)

        _section_label(cols, "LOGIN (ACCOUNT NUMBER)")
        login_e = ctk.CTkEntry(
            cols, textvariable=self._v_login,
            placeholder_text="123456789",
            fg_color=C["bg_input"], border_color=C["border"],
            text_color=C["text"], placeholder_text_color=C["muted2"],
            font=(FF, 12), height=34, corner_radius=6,
        )
        login_e.grid(row=1, column=0, sticky="ew", padx=(0, 6))

        _section_label_r = ctk.CTkLabel(
            cols, text="SERVER",
            font=(FF, 9, "bold"), text_color=C["muted"], anchor="w",
        )
        _section_label_r.grid(row=0, column=1, sticky="w", pady=(12, 2))
        server_e = ctk.CTkEntry(
            cols, textvariable=self._v_server,
            placeholder_text="ICMarkets-Live01",
            fg_color=C["bg_input"], border_color=C["border"],
            text_color=C["text"], placeholder_text_color=C["muted2"],
            font=(FF, 12), height=34, corner_radius=6,
        )
        server_e.grid(row=1, column=1, sticky="ew")

        # Password
        _section_label(self._form_container, "PASSWORD")
        pw_frame = ctk.CTkFrame(self._form_container, fg_color="transparent")
        pw_frame.pack(fill="x")
        pw_frame.grid_columnconfigure(0, weight=1)
        self._pw_entry = ctk.CTkEntry(
            pw_frame, textvariable=self._v_password,
            show="●",
            placeholder_text="Your MT5 password",
            fg_color=C["bg_input"], border_color=C["border"],
            text_color=C["text"], placeholder_text_color=C["muted2"],
            font=(FF, 12), height=34, corner_radius=6,
        )
        self._pw_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self._show_pw_btn = ctk.CTkButton(
            pw_frame, text="👁", width=34, height=34,
            fg_color=C["bg_input"], hover_color=C["bg_hover"],
            text_color=C["muted"], border_width=1, border_color=C["border"],
            corner_radius=6, font=(FF, 14),
            command=self._toggle_password,
        )
        self._show_pw_btn.grid(row=0, column=1)

        _divider(self._form_container)

        # ── Terminal path ────────────────────────────────────────────────────
        _section_label(self._form_container, "MT5 TERMINAL PATH")
        path_frame = ctk.CTkFrame(self._form_container, fg_color="transparent")
        path_frame.pack(fill="x", pady=(0, 4))
        path_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(
            path_frame, textvariable=self._v_path,
            placeholder_text="C:\\Program Files\\MetaTrader 5\\terminal64.exe",
            fg_color=C["bg_input"], border_color=C["border"],
            text_color=C["text"], placeholder_text_color=C["muted2"],
            font=(FF, 11), height=34, corner_radius=6,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkButton(
            path_frame, text="Browse…", width=80, height=34,
            fg_color=C["bg_input"], hover_color=C["bg_hover"],
            text_color=C["muted"], border_width=1, border_color=C["border"],
            corner_radius=6, font=(FF, 11),
            command=self._browse_path,
        ).grid(row=0, column=1)

        ctk.CTkLabel(
            self._form_container,
            text="Each broker account needs its own MT5 terminal installation.",
            font=(FF, 10), text_color=C["muted"], anchor="w",
        ).pack(fill="x")

        _divider(self._form_container)

        # ── Lot settings (slaves only) ───────────────────────────────────────
        self._lot_section = ctk.CTkFrame(self._form_container, fg_color="transparent")
        self._lot_section.pack(fill="x")

        _section_label(self._lot_section, "LOT MODE")
        lot_mode_frame = ctk.CTkFrame(self._lot_section, fg_color="transparent")
        lot_mode_frame.pack(fill="x")

        self._lot_mult_btn = ctk.CTkButton(
            lot_mode_frame, text="Multiplier",
            height=32, font=(FF, 11),
            fg_color=C["cyan_dim"], hover_color=C["cyan"],
            text_color="#000", corner_radius=6,
            command=lambda: self._set_lot_mode("multiplier"),
        )
        self._lot_mult_btn.pack(side="left", padx=(0, 6))

        self._lot_fixed_btn = ctk.CTkButton(
            lot_mode_frame, text="Fixed Lot",
            height=32, font=(FF, 11),
            fg_color=C["bg_input"], hover_color=C["bg_hover"],
            text_color=C["muted"], border_width=1, border_color=C["border"],
            corner_radius=6,
            command=lambda: self._set_lot_mode("fixed"),
        )
        self._lot_fixed_btn.pack(side="left")

        lot_val_row = ctk.CTkFrame(self._lot_section, fg_color="transparent")
        lot_val_row.pack(fill="x", pady=(8, 0))

        self._lot_desc = ctk.CTkLabel(
            lot_val_row,
            text="Multiplier: slave lot = master lot × value",
            font=(FF, 10), text_color=C["muted"], anchor="w",
        )
        self._lot_desc.pack(side="left")

        ctk.CTkEntry(
            lot_val_row, textvariable=self._v_lot_value,
            width=72, height=32,
            fg_color=C["bg_input"], border_color=C["border"],
            text_color=C["text"], font=(FF, 12), corner_radius=6,
        ).pack(side="right")

        _divider(self._form_container)

        # ── Enabled toggle + bottom actions ─────────────────────────────────
        bottom = ctk.CTkFrame(self._form_container, fg_color="transparent")
        bottom.pack(fill="x", pady=(12, 0))

        self._enabled_switch = ctk.CTkSwitch(
            bottom,
            text="Account Enabled",
            variable=self._v_enabled,
            font=(FF, 12),
            text_color=C["text"],
            progress_color=C["cyan_dim"],
            button_color=C["cyan"],
            button_hover_color=C["cyan"],
        )
        self._enabled_switch.pack(side="left")

        self._delete_btn = ctk.CTkButton(
            bottom,
            text="🗑  Delete",
            width=90, height=32,
            font=(FF, 11),
            fg_color=C["rose_faint"],
            hover_color=C["rose_dim"],
            text_color=C["rose"],
            border_width=1, border_color=C["rose_dim"],
            corner_radius=6,
            command=self._delete_current,
        )
        self._delete_btn.pack(side="right")

        self._apply_btn = ctk.CTkButton(
            bottom,
            text="✓  Apply",
            width=90, height=32,
            font=(FF, 11, "bold"),
            fg_color=C["bg_input"],
            hover_color=C["cyan_faint"],
            text_color=C["cyan"],
            border_width=1, border_color=C["cyan_faint"],
            corner_radius=6,
            command=self._apply_current,
        )
        self._apply_btn.pack(side="right", padx=(0, 8))

        # Test connection button
        self._test_btn = ctk.CTkButton(
            bottom,
            text="⚡ Test",
            width=80, height=32,
            font=(FF, 11),
            fg_color=C["bg_input"],
            hover_color=C["bg_hover"],
            text_color=C["yellow"],
            border_width=1, border_color=C["muted2"],
            corner_radius=6,
            command=self._test_connection,
        )
        self._test_btn.pack(side="right", padx=(0, 8))

        self._test_result_lbl = ctk.CTkLabel(
            self._form_container, text="",
            font=(FF, 11), text_color=C["muted"],
        )
        self._test_result_lbl.pack(fill="x", pady=(8, 0))

    # ── Selection & form population ───────────────────────────────────────────

    def _select_account(self, account: dict, is_master: bool):
        self._current_account = account
        self._current_is_master = is_master

        # Update row highlights
        for row in self._rows:
            row.set_selected(row._account is account)

        # Show form
        self._no_selection_lbl.pack_forget()
        self._form_container.pack(fill="both", expand=True)

        # Populate fields
        self._v_name.set(account.get("name", ""))
        self._v_login.set(str(account.get("login", "")))
        self._v_password.set(str(account.get("password", "")))
        self._v_server.set(account.get("server", ""))
        self._v_path.set(account.get("terminal_path", ""))
        self._v_lot_mode.set(account.get("lot_mode", "multiplier"))
        self._v_lot_value.set(str(account.get("lot_value", "1.0")))
        self._v_enabled.set(account.get("enabled", True))

        # Role badge
        if is_master:
            self._form_role_lbl.configure(text="MASTER", text_color=C["cyan"])
        else:
            self._form_role_lbl.configure(text="SLAVE", text_color=C["rose"])

        # Lot section: only show for slaves
        if is_master:
            self._lot_section.pack_forget()
            self._enabled_switch.pack_forget()
            self._delete_btn.configure(state="disabled", text_color=C["muted2"],
                                       border_color=C["muted2"])
        else:
            self._lot_section.pack(fill="x")
            self._enabled_switch.pack(side="left")
            self._delete_btn.configure(state="normal", text_color=C["rose"],
                                       border_color=C["rose_dim"])

        self._set_lot_mode(account.get("lot_mode", "multiplier"), silent=True)
        self._test_result_lbl.configure(text="")

    # ── Form actions ──────────────────────────────────────────────────────────

    def _apply_current(self):
        """Write form values back into the in-memory dict."""
        if self._current_account is None:
            return

        try:
            login = int(self._v_login.get().strip())
        except ValueError:
            self._flash_status("⚠ Login must be a number", C["yellow"])
            return

        try:
            lot_val = float(self._v_lot_value.get().strip())
        except ValueError:
            lot_val = 1.0

        self._current_account.update({
            "name":          self._v_name.get().strip(),
            "login":         login,
            "password":      self._v_password.get(),
            "server":        self._v_server.get().strip(),
            "terminal_path": self._v_path.get().strip(),
            "lot_mode":      self._v_lot_mode.get(),
            "lot_value":     lot_val,
            "enabled":       bool(self._v_enabled.get()),
        })

        self._refresh_list()
        self._flash_status("✓ Applied (not saved yet — click Save All)", C["yellow"])

    def _delete_current(self):
        if self._current_account is None or self._current_is_master:
            return
        if not messagebox.askyesno(
            "Delete Account",
            f"Delete '{self._current_account.get('name', 'this account')}'?\n"
            "This cannot be undone.",
            parent=self,
        ):
            return
        self._slaves_raw = [s for s in self._slaves_raw if s is not self._current_account]
        self._current_account = None
        self._refresh_list()
        self._no_selection_lbl.pack(pady=40)
        self._form_container.pack_forget()
        self._flash_status("Deleted (click Save All to persist)", C["rose"])

    def _add_slave(self):
        new_slave = {
            "name":          "New Slave Account",
            "login":         0,
            "password":      "",
            "server":        "",
            "terminal_path": "C:\\Program Files\\MetaTrader 5\\terminal64.exe",
            "enabled":       True,
            "lot_mode":      "multiplier",
            "lot_value":     1.0,
        }
        self._slaves_raw.append(new_slave)
        self._refresh_list()
        self._select_account(new_slave, is_master=False)
        # Scroll to bottom
        self.after(50, lambda: self._list_scroll._parent_canvas.yview_moveto(1.0))

    def _save_all(self):
        """Apply current form then write entire config to accounts.json."""
        self._apply_current()
        try:
            self._write_raw()
        except Exception as e:
            self._flash_status(f"Save failed: {e}", C["red"])
            return

        # Callback to the main app
        self.on_save(self._master_raw, self._slaves_raw, self._settings)
        self._flash_status("✓  Saved to accounts.json", C["green"])

    # ── Password toggle ───────────────────────────────────────────────────────

    def _toggle_password(self):
        current = self._pw_entry.cget("show")
        self._pw_entry.configure(show="" if current == "●" else "●")

    # ── Browse for exe ────────────────────────────────────────────────────────

    def _browse_path(self):
        path = filedialog.askopenfilename(
            parent=self,
            title="Select MT5 terminal64.exe",
            filetypes=[("MT5 Terminal", "terminal64.exe"), ("Executables", "*.exe")],
            initialdir="C:\\Program Files",
        )
        if path:
            self._v_path.set(path.replace("/", "\\"))

    # ── Lot mode toggle ───────────────────────────────────────────────────────

    def _set_lot_mode(self, mode: str, silent: bool = False):
        self._v_lot_mode.set(mode)
        if mode == "multiplier":
            self._lot_mult_btn.configure(fg_color=C["cyan_dim"], text_color="#000")
            self._lot_fixed_btn.configure(fg_color=C["bg_input"], text_color=C["muted"])
            self._lot_desc.configure(text="slave lot = master lot × value")
        else:
            self._lot_mult_btn.configure(fg_color=C["bg_input"], text_color=C["muted"])
            self._lot_fixed_btn.configure(fg_color=C["rose_dim"], text_color="#000")
            self._lot_desc.configure(text="Always trade this exact lot size")
        if not silent and self._current_account is not None:
            self._current_account["lot_mode"] = mode

    # ── Test connection ───────────────────────────────────────────────────────

    def _test_connection(self):
        self._apply_current()
        if not self._current_account:
            return
        self._test_btn.configure(state="disabled", text="Testing…")
        self._test_result_lbl.configure(text="Connecting…", text_color=C["yellow"])

        account = dict(self._current_account)
        threading.Thread(
            target=self._test_connection_bg,
            args=(account,),
            daemon=True,
        ).start()

    def _test_connection_bg(self, account: dict):
        try:
            from src.models import AccountConfig, LotMode
            from src.mt5_connector import MT5Connector
            import MetaTrader5 as mt5

            cfg = AccountConfig(
                name=account.get("name", ""),
                login=int(account.get("login", 0)),
                password=str(account.get("password", "")),
                server=account.get("server", ""),
                terminal_path=account.get("terminal_path", ""),
            )
            conn = MT5Connector(cfg)
            ok = conn.connect(timeout_ms=8000)
            if ok:
                info = mt5.account_info()
                if info:
                    msg = f"✓  Connected — balance {info.balance:,.2f} {info.currency}"
                    color = C["green"]
                else:
                    msg, color = "✓  Connected (no account info)", C["yellow"]
                conn.shutdown()
            else:
                msg, color = "✗  Connection failed — check credentials and server", C["red"]
        except ImportError:
            msg = "MetaTrader5 not installed or MT5 terminal not running"
            color = C["red"]
        except Exception as e:
            msg, color = f"✗  Error: {e}", C["red"]

        self.after(0, lambda: self._test_result_lbl.configure(text=msg, text_color=color))
        self.after(0, lambda: self._test_btn.configure(state="normal", text="⚡ Test"))

    # ── Status flash ──────────────────────────────────────────────────────────

    def _flash_status(self, msg: str, color: str):
        self._status_lbl.configure(text=msg, text_color=color)
        self.after(4000, lambda: self._status_lbl.configure(text=""))
