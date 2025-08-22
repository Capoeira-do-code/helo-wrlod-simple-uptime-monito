#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Helo Wrlod - Simple Uptime Monitoy — Multi‑Host, Clean UI, Popup Add Host (Tkinter, no extra deps)

Compat: Python 3.7+

Novedades añadidas vs v2 y v1
- Modo ultracompacto y opción de ordenar por latencia, host..
- Modo claro y oscuro
- Traducido al inglés ( I won't do multiple languages support. If you wan't to translate it feel free to change the texts)
- Modo compacto 100% fachero


Estructura
  main.py
  /resources
   offline.png
   online.pgn
  resources/online.png, resources/offline.png, /// resources/background.png (opcional) --> Idk if it works, it should but idk ///
"""

import os
import sys
import json
import socket
import time
import threading
import queue
import argparse
from dataclasses import dataclass, asdict
from typing import Optional, List, Tuple

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

# ----------------------------- Core logic ---------------------------------- #

def tcp_ping(host: str, port: int, timeout: float = 2.0):
    """Open a TCP connection and measure connect latency.
    Returns (ok: bool, latency_ms: float|None, error: str|None)
    """
    t0 = time.perf_counter()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        latency = (time.perf_counter() - t0) * 1000.0
        try:
            s.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        s.close()
        return True, latency, None
    except Exception as e:
        try:
            s.close()
        except Exception:
            pass
        return False, None, str(e)


@dataclass
class MonitorConfig:
    host: str = "example.com"
    port: int = 22
    interval: float = 2.0
    timeout: float = 2.0
    sound_on_change: bool = True


class MonitorThread(threading.Thread):
    def __init__(self, cfg: MonitorConfig, out_q: "queue.Queue"):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.out_q = out_q
        self._stop = threading.Event()
        self._failures = 0
        self._max_backoff = 30.0  # seconds

    def stop(self):
        self._stop.set()

    def run(self):
        while not self._stop.is_set():
            started = time.time()
            ok, latency, err = tcp_ping(self.cfg.host, self.cfg.port, self.cfg.timeout)
            self.out_q.put({
                "ts": time.time(),
                "ok": ok,
                "latency": latency,
                "error": err,
                "host": self.cfg.host,
                "port": self.cfg.port,
            })
            # backoff logic
            if ok:
                self._failures = 0
            else:
                self._failures = min(self._failures + 1, 10)
            elapsed = time.time() - started
            base = self.cfg.interval
            backoff = min(self._max_backoff, base * (2 ** self._failures)) if self._failures > 0 else base
            wait = max(0.0, backoff - elapsed)
            if self._stop.wait(wait):
                break


# ----------------------------- UI helpers ---------------------------------- #

APP_DIR = os.path.dirname(os.path.abspath(__file__))
RES_DIR = os.path.join(APP_DIR, "resources")
CFG_PATH = os.path.join(APP_DIR, "config.json")


def load_image(name: str):
    path = os.path.join(RES_DIR, name)
    try:
        return tk.PhotoImage(file=path)
    except Exception:
        return None


class Sparkline(ttk.Frame):
    """Tiny line chart of recent latency/health."""
    def __init__(self, master, max_points=80, palette=None):
        ttk.Frame.__init__(self, master)
        self.max_points = max_points
        self.points = []  # type: List[Tuple[float, bool]]
        self.palette = palette or {
            'spark_bg': '#0b0f14', 'spark_base': '#0b0f14', 'ok': '#22c55e', 'bad': '#ef4444'
        }
        self.canvas = tk.Canvas(self, height=28, highlightthickness=0, bg=self.palette['spark_bg'], bd=0)
        self.canvas.pack(fill="x", expand=True)
        self.canvas.bind("<Configure>", lambda e: self._redraw())

    def set_palette(self, palette: dict):
        self.palette.update(palette)
        try:
            self.canvas.configure(bg=self.palette['spark_bg'])
        except Exception:
            pass
        self._redraw()

    def push(self, latency_ms, ok):
        v = (latency_ms or 0.0, bool(ok))
        self.points.append(v)
        if len(self.points) > self.max_points:
            self.points.pop(0)
        self._redraw()

    def last_latency(self) -> float:
        for lat, ok in reversed(self.points):
            if ok:
                return float(lat)
        return 1e12  

    def _redraw(self):
        c = self.canvas
        c.delete("all")
        w = max(1, int(c.winfo_width()))
        h = max(28, int(c.winfo_height()))
        if not self.points:
            return
        c.create_rectangle(0, 0, w, h, fill=self.palette['spark_bg'], outline="")
        c.create_line(0, h - 9, w, h - 9, fill=self.palette['spark_base'])
        lat_vals = [p[0] for p in self.points if p[1]] or [0.0]
        lat_vals_sorted = sorted(lat_vals)
        p95_index = int(max(0, int(len(lat_vals_sorted) * 0.95) - 1)) if lat_vals_sorted else 0
        p95 = lat_vals_sorted[p95_index] if lat_vals_sorted else 0.0
        max_v = max(20.0, p95 or 20.0)
        denom = max(1, len(self.points) - 1)
        step_x = float(w) / float(denom)
        prev = None
        for i, (lat, ok) in enumerate(self.points):
            x = i * step_x
            y = h - 9 - (min(lat, max_v) / max_v) * (h - 12)
            if prev is not None:
                x1, y1, ok1 = prev
                c.create_line(x1, y1, x, y, fill=self.palette['ok'] if (ok1 and ok) else self.palette['bad'], width=2)
            prev = (x, y, ok)


class GlassCard(ttk.Frame):
    def __init__(self, master):
        ttk.Frame.__init__(self, master, style="Card.TFrame")
        self.top_line = tk.Canvas(self, height=8, highlightthickness=0, bg="#0f172a", bd=0)
        self.top_line.pack(fill="x", side="top")
        self.top_line.create_line(12, 7, 9999, 7, fill="#0a0a0a")


# ----------------------------- Host Panel ---------------------------------- #

class HostPanel(GlassCard):
    def __init__(self, master, images, cfg: MonitorConfig, on_remove, get_defaults, palette, ultra_var, on_update):
        GlassCard.__init__(self, master)
        self.images = images
        self.cfg = cfg
        self.on_remove = on_remove
        self.get_defaults = get_defaults  # devuelve intervalo, timeout
        self.palette = palette
        self.ultra_var = ultra_var
        self.on_update = on_update  

        self.out_q = queue.Queue()
        self.worker = None  
        self._hide_job = None
        self._menu_btn_visible = False
        self._last_ok = None
        self._last_latency = 1e12

        # Row: icon LEFT + texts + (menu at right)
        row = ttk.Frame(self)
        row.pack(fill="x", padx=16, pady=(8, 6))
        self._hover_bind(row)

        self.icon = tk.Label(row, image=(self.images.get('online') or ""))
        self.icon.pack(side="left", padx=(0, 10))

        col = ttk.Frame(row)
        col.pack(side="left", fill="x", expand=True)
        self.status_lbl = ttk.Label(col, text="Idle", font=("Segoe UI", 18, "bold"))
        self.status_lbl.pack(anchor="w")
        self.detail_lbl = ttk.Label(col, text="—", font=("Segoe UI", 10))
        self.detail_lbl.pack(anchor="w", pady=(2, 0))

        # Kebab (⋮) menu button — hidden by default; appears on hover
        right = ttk.Frame(row)
        right.pack(side="right")
        self.menu_btn = ttk.Button(right, text="⋮", width=2, style="Icon.TButton")
        self.menu_btn.pack_forget()
        self.menu_btn.bind("<Enter>", self._show_menu_btn)
        self.menu_btn.bind("<Leave>", self._schedule_hide_menu_btn)

        self._menu = tk.Menu(self, tearoff=0)
        self._menu.add_command(label="Remove", command=self.remove)
        self.menu_btn.bind("<Button-1>", self._post_menu)

        # Sparkline
        self.spark = Sparkline(self, max_points=100, palette=self.palette)
        self.spark.pack(fill="x", padx=16, pady=(0, 10))

        # react to ultracompact toggle
        self.ultra_var.trace_add('write', lambda *a: self._apply_density())
        self._apply_density()

        self.after(150, self._poll_results)
        self.start()  # auto-start

    # --- hover helpers
    def _hover_bind(self, widget):
        widget.bind("<Enter>", self._show_menu_btn)
        widget.bind("<Leave>", self._schedule_hide_menu_btn)
        for child in getattr(widget, 'winfo_children', lambda: [])():
            try:
                self._hover_bind(child)
            except Exception:
                pass

    def _show_menu_btn(self, event=None):
        if self._hide_job:
            try:
                self.after_cancel(self._hide_job)
            except Exception:
                pass
            self._hide_job = None
        if not self._menu_btn_visible:
            self.menu_btn.pack(side="right")
            self._menu_btn_visible = True

    def _schedule_hide_menu_btn(self, event=None):
        if self._hide_job:
            try:
                self.after_cancel(self._hide_job)
            except Exception:
                pass
        self._hide_job = self.after(300, self._hide_menu_btn)

    def _hide_menu_btn(self):
        self._hide_job = None
        if self._menu_btn_visible:
            self.menu_btn.pack_forget()
            self._menu_btn_visible = False

    def _post_menu(self, event=None):
        try:
            x = self.menu_btn.winfo_rootx()
            y = self.menu_btn.winfo_rooty() + self.menu_btn.winfo_height()
            try:
                self._menu.tk_popup(x, y)
            except Exception:
                self._menu.post(x, y)
        finally:
            try:
                self._menu.grab_release()
            except Exception:
                pass

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        host = self.cfg.host.strip()
        if not host:
            return
        d_interval, d_timeout = self.get_defaults()
        self.cfg.interval = max(0.5, d_interval)
        self.cfg.timeout = max(0.5, d_timeout)
        self.out_q = queue.Queue()
        self.worker = MonitorThread(self.cfg, self.out_q)
        self.worker.start()
        self.status_lbl.config(text="Checking {}:{}...".format(self.cfg.host, self.cfg.port))

    def stop(self):
        if self.worker:
            self.worker.stop()
            self.worker = None

    def _poll_results(self):
        try:
            while True:
                data = self.out_q.get_nowait()
                self._apply_result(data)
        except queue.Empty:
            pass
        self.after(120, self._poll_results)

    def _apply_result(self, data):
        ok = bool(data.get("ok"))
        host = data.get("host")
        port = data.get("port")
        latency = data.get("latency")
        err = data.get("error")
        if ok:
            img = self.images.get('online')
            if img:
                self.icon.config(image=img)
            text = "ONLINE"
            detail = "{}:{} reachable — {:.0f} ms".format(host, port, latency or 0)
            color = self.palette.get('ok', '#22c55e')
        else:
            img = self.images.get('offline')
            if img:
                self.icon.config(image=img)
            text = "OFFLINE"
            detail = "{}:{} unreachable — {}".format(host, port, err or 'no response')
            color = self.palette.get('bad', '#ef4444')
        self.status_lbl.config(text=text, foreground=color)
        # In ultracompact, show condensed text
        if bool(self.ultra_var.get()):
            if ok:
                self.detail_lbl.config(text="{}:{} • {:.0f} ms".format(host, port, (latency or 0)))
            else:
                self.detail_lbl.config(text="{}:{} • offline".format(host, port))
        else:
            self.detail_lbl.config(text=detail)

        self.spark.push(latency, ok)
        self._last_ok = ok
        self._last_latency = float(latency or 1e12)
        # notify app for autosort
        try:
            self.on_update()
        except Exception:
            pass

    def get_cfg(self) -> MonitorConfig:
        return self.cfg

    def last_ok(self) -> bool:
        return bool(self._last_ok)

    def last_latency(self) -> float:
        return float(self._last_latency)

    def set_palette(self, palette: dict):
        self.palette = palette
        self.spark.set_palette({
            'spark_bg': palette['spark_bg'],
            'spark_base': palette['spark_base'],
            'ok': palette['ok'],
            'bad': palette['bad'],
        })
        # update text color
        try:
            self.status_lbl.configure(foreground=palette.get('text', '#e5e7eb'))
            self.detail_lbl.configure(foreground=palette.get('text', '#e5e7eb'))
        except Exception:
            pass

    def _apply_density(self):
        ultra = bool(self.ultra_var.get())
        try:
            if ultra:
                self.spark.pack_forget()
                self.status_lbl.configure(font=("Segoe UI", 14, "bold"))
                self.detail_lbl.configure(font=("Segoe UI", 10))
            else:
                if str(self.spark) not in [str(w) for w in self.pack_slaves()]:
                    self.spark.pack(fill="x", padx=16, pady=(0, 10))
                self.status_lbl.configure(font=("Segoe UI", 18, "bold"))
                self.detail_lbl.configure(font=("Segoe UI", 10))
        except Exception:
            pass

    def remove(self):
        self.stop()
        self.on_remove(self)
        self.destroy()


#Añadir host popup

class AddHostDialog(tk.Toplevel):
    def __init__(self, master, on_submit, palette):
        tk.Toplevel.__init__(self, master)
        self.title("Add Host")
        self.configure(bg=palette['bg'])
        self.transient(master)
        self.grab_set()
        self.resizable(False, False)

        frm = ttk.Frame(self, style="Card.TFrame")
        frm.pack(padx=32, pady=32, fill="x")

        # Título botón
        ttk.Label(frm, text="Add New Host", font=("Segoe UI", 18, "bold"), foreground=palette['text']).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 18))

        ttk.Label(frm, text="Host", font=("Segoe UI", 14, "bold"), foreground=palette['text']).grid(row=1, column=0, sticky="w")
        self.host_var = tk.StringVar(value="")
        host_entry = ttk.Entry(frm, textvariable=self.host_var, width=32, font=("Segoe UI", 12))
        host_entry.grid(row=2, column=0, columnspan=2, sticky="we", pady=(0, 12))
        host_entry.configure(foreground=palette['entry_fg'], background=palette['entry_bg'])

        ttk.Label(frm, text="Port", font=("Segoe UI", 14, "bold"), foreground=palette['text']).grid(row=3, column=0, sticky="w")
        self.port_var = tk.IntVar(value=22)
        port_spin = ttk.Spinbox(frm, from_=1, to=65535, textvariable=self.port_var, width=10, font=("Segoe UI", 12))
        port_spin.grid(row=4, column=0, sticky="w", pady=(0, 12))
        # input modo oscuro brrrrr
        if palette['bg'] == "#0B0F14":
            port_spin.configure(background=palette['entry_bg'], foreground=palette['entry_fg'])

        # botones
        btns = ttk.Frame(frm, style="Card.TFrame")
        btns.grid(row=5, column=0, columnspan=2, sticky="e", pady=(18, 0))
        ttk.Button(btns, text="Cancel", style="Ghost.TButton", command=self.destroy).pack(side="right", padx=(0, 8))
        ttk.Button(btns, text="Add", style="Ghost.TButton", command=lambda: on_submit(self.host_var.get().strip(), int(self.port_var.get() or 22)) or self.destroy()).pack(side="right")

        # Center popup
        self.update_idletasks()
        parent_x = master.winfo_rootx()
        parent_y = master.winfo_rooty()
        parent_w = master.winfo_width()
        parent_h = master.winfo_height()
        win_w = self.winfo_width()
        win_h = self.winfo_height()
        x = parent_x + (parent_w // 2) - (win_w // 2)
        y = parent_y + (parent_h // 2) - (win_h // 2)
        self.geometry(f"+{x}+{y}")


# MAIN APP #

class App(tk.Tk):
    def __init__(self, cfg: MonitorConfig):
        tk.Tk.__init__(self)
        self.title("Helo Wrlod - Simple Uptime Monitor")
        self.geometry("820x620")
        self.minsize(760, 580)
        self.theme_var = tk.StringVar(value=self._load_theme())
        self.ultra_var = tk.BooleanVar(value=self._load_ultra())
        self.sort_by = tk.StringVar(value=self._load_sort())  # 'status' | 'latency' | 'host'
        self.auto_sort_var = tk.BooleanVar(value=self._load_autosort())
        self._apply_style()  # uses theme_var

        # imagenes
        self.img_online = load_image("online.png")
        self.img_offline = load_image("offline.png")
        self.images = {'online': self.img_online, 'offline': self.img_offline}

        # defaults (interval/timeout) persisted
        self.default_interval, self.default_timeout = self._load_defaults()

        # fondo ( opcional pero por si algun dia se quiere brrrrrr)
        bg_img = load_image("background.png")
        if bg_img is not None:
            self.bg_label = tk.Label(self, image=bg_img, borderwidth=0)
            self.bg_label.image = bg_img
            self.bg_label.place(x=0, y=0, relwidth=1, relheight=1)
        else:
            self.bg_label = None

        # header, titulo y boton de add host
        header = ttk.Frame(self)
        header.pack(fill="x", padx=20, pady=(14, 8))
        self.header_icon = tk.Label(header, image=(self.img_online or ""), bg=self.palette['bg'])
        self.header_icon.pack(side="left", padx=(0, 12))
        ttk.Label(header, text="Simple Uptime Monitor", font=("Segoe UI", 18, "bold")).pack(side="left")
        ttk.Button(header, text="+ Add Host", style="Ghost.TButton", command=self.open_add_host).pack(side="right")

        # scrollable container for hosts
        container = ttk.Frame(self)
        container.pack(fill="both", expand=True, padx=20, pady=(0, 16))

        self.hosts_canvas = tk.Canvas(container, highlightthickness=0, bd=0, bg=self.palette['bg'])
        vbar = ttk.Scrollbar(container, orient='vertical', command=self.hosts_canvas.yview)
        self.hosts_canvas.configure(yscrollcommand=vbar.set)
        self.hosts_canvas.pack(side='left', fill='both', expand=True)
        vbar.pack(side='right', fill='y')

        self.hosts_frame = ttk.Frame(self.hosts_canvas)
        self._canvas_window = self.hosts_canvas.create_window((0, 0), window=self.hosts_frame, anchor='nw')
        self.hosts_frame.bind('<Configure>', lambda e: self.hosts_canvas.configure(scrollregion=self.hosts_canvas.bbox('all')))
        self.hosts_canvas.bind('<Configure>', lambda e: self.hosts_canvas.itemconfigure(self._canvas_window, width=e.width))
        self.hosts_canvas.bind('<Enter>', lambda e: self._toggle_mousewheel(True))
        self.hosts_canvas.bind('<Leave>', lambda e: self._toggle_mousewheel(False))

        # load config or create first panel
        configs = self._load_configs()
        if not configs:
            configs = [cfg]
        self.panels = []  
        for c in configs:
            self._create_panel(c)
        # initial sort
        self._sort_panels()

        # menu & shortcuts
        self._build_menu()
        self.bind_all("<Control-s>", lambda e: self._start_stop_all())
        self.bind_all("<Control-q>", lambda e: self._on_close())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- modo claro / oscuro
    def _get_palette(self):
        if self.theme_var.get() == 'light':
            return {
                'bg': '#ffffff', 'card': '#ffffff', 'text': '#111827',
                'ghost_bg': '#ffffff', 'ghost_active': '#e5e7eb',
                'entry_fg': '#e5e7eb', 'entry_bg': '#ffffff',
                'spark_bg': '#ffffff', 'spark_base': '#e5e7eb',
                'ok': '#22c55e', 'bad': '#ef4444'
            }
        else:
            return {
                'bg': '#0b0f14', 'card': '#0B0F14', 'text': '#e5e7eb',
                'ghost_bg': '#0f172a', 'ghost_active': '#111827',
                'entry_fg': '#e5e7eb', 'entry_bg': '#0B0F14',
                'spark_bg': '#0b0f14', 'spark_base': '#0B0F14',
                'ok': '#22c55e', 'bad': '#ef4444'
            }

    def _apply_style(self):
        self.palette = self._get_palette()
        self.configure(bg=self.palette['bg'])
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TFrame", background=self.palette['bg'])
        style.configure("TLabel", background=self.palette['bg'], foreground=self.palette['text'])
        style.configure("TButton", padding=9)
        style.configure("Card.TFrame", background=self.palette['card'])
        style.map("TButton", background=[("active", self.palette['ghost_active'])])
        style.configure("TCheckbutton", background=self.palette['bg'], foreground=self.palette['text'])
        style.configure("TEntry", fieldbackground=self.palette['entry_bg'], foreground=self.palette['entry_fg'])
        style.configure("TSpinbox", arrowsize=14)
        style.configure("Ghost.TButton", padding=9, background=self.palette['ghost_bg'], foreground=self.palette['text'], relief="flat")
        style.map("Ghost.TButton", background=[('active', self.palette['ghost_active'])])
        style.configure("Icon.TButton", padding=4)
        style.map("Icon.TButton", background=[('active', self.palette['ghost_active'])])

    def _set_theme(self, theme):
        try:
            self.theme_var.set(theme)
        except Exception:
            pass
        self._apply_style()
        try:
            self.header_icon.configure(bg=self.palette['bg'])
            self.hosts_canvas.configure(bg=self.palette['bg'])
        except Exception:
            pass
        for p in getattr(self, 'panels', []):
            p.set_palette({
                'spark_bg': self.palette['spark_bg'],
                'spark_base': self.palette['spark_base'],
                'ok': self.palette['ok'],
                'bad': self.palette['bad'],
            })
        self._persist_defaults()

    def _load_theme(self):
        try:
            with open(CFG_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            t = raw.get('theme', 'dark')
            return 'light' if str(t).lower().startswith('light') else 'dark'
        except Exception:
            return 'dark'

    #menu
    def _build_menu(self):
        m = tk.Menu(self)
        filem = tk.Menu(m, tearoff=0)
        filem.add_command(label="+ Add Host", command=self.open_add_host)
        filem.add_separator(); filem.add_command(label="Start/Stop All (Ctrl+S)", command=self._start_stop_all)
        filem.add_separator(); filem.add_command(label="Exit (Ctrl+Q)", command=self._on_close)
        m.add_cascade(label="File", menu=filem)

        viewm = tk.Menu(m, tearoff=0)
        viewm.add_command(label="Set Interval…", command=self._set_interval)
        viewm.add_command(label="Set Timeout…", command=self._set_timeout)
        viewm.add_separator()
        viewm.add_radiobutton(label="Light mode", variable=self.theme_var, value='light', command=lambda: self._set_theme('light'))
        viewm.add_radiobutton(label="Dark mode", variable=self.theme_var, value='dark', command=lambda: self._set_theme('dark'))
        viewm.add_separator()
        viewm.add_checkbutton(label="Ultra-compact mode", onvalue=True, offvalue=False, variable=self.ultra_var, command=self._on_toggle_ultra)
        viewm.add_separator()
        sortm = tk.Menu(viewm, tearoff=0)
        sortm.add_radiobutton(label="by status", variable=self.sort_by, value='status', command=self._sort_panels)
        sortm.add_radiobutton(label="by latency", variable=self.sort_by, value='latency', command=self._sort_panels)
        sortm.add_radiobutton(label="by host", variable=self.sort_by, value='host', command=self._sort_panels)
        sortm.add_checkbutton(label="Auto-sort al actualizar (buggy)", onvalue=True, offvalue=False, variable=self.auto_sort_var)
        viewm.add_cascade(label="Sort", menu=sortm)
        viewm.add_separator()
        viewm.add_command(label="Opacity 50%", command=lambda: self._set_alpha(0.5))
        viewm.add_command(label="Opacity 70%", command=lambda: self._set_alpha(0.7))
        viewm.add_command(label="Opacity 85%", command=lambda: self._set_alpha(0.85))
        viewm.add_command(label="Opacity 100%", command=lambda: self._set_alpha(1.0))
        viewm.add_command(label="for more options, edit the code")
        m.add_cascade(label="View", menu=viewm)

        helpm = tk.Menu(m, tearoff=0)
        helpm.add_command(label="About", command=lambda: messagebox.showinfo("About", "Helo Wrlod - Simple uptime monitor\nby Capoeira do code\n& ChatGPT ofc\nI code very fast, hire me thank you"))
        helpm.add_command(label="Racism button", command=lambda: messagebox.showinfo("Racism button", "Whatttt????? Why you clicked here? racism bad :C. don't be resist"))
        m.add_cascade(label="Help", menu=helpm)
        self.config(menu=m)

    # ---- menu actions
    def _set_interval(self):
        try:
            val = simpledialog.askfloat("Set Interval", "Seconds between checks:", minvalue=0.5, initialvalue=self.default_interval, parent=self)
            if val is None:
                return
            self.default_interval = max(0.5, float(val))
            for p in self.panels:
                p.cfg.interval = self.default_interval
            self._persist_defaults()
        except Exception:
            pass

    def _set_timeout(self):
        try:
            val = simpledialog.askfloat("Set Timeout", "TCP timeout (seconds):", minvalue=0.5, initialvalue=self.default_timeout, parent=self)
            if val is None:
                return
            self.default_timeout = max(0.5, float(val))
            for p in self.panels:
                p.cfg.timeout = self.default_timeout
            self._persist_defaults()
        except Exception:
            pass

    def _on_toggle_ultra(self):
        for p in self.panels:
            try:
                p._apply_density()
            except Exception:
                pass
        self._persist_defaults()

    def _set_alpha(self, a):
        try:
            self.attributes("-alpha", a)
        except Exception:
            pass
        self._persist_defaults()

    # ---- scroll wheel enable/disable
    def _toggle_mousewheel(self, enable: bool):
        if enable:
            try:
                self.bind_all("<MouseWheel>", self._on_mousewheel)
                self.bind_all("<Button-4>", self._on_mousewheel_linux)
                self.bind_all("<Button-5>", self._on_mousewheel_linux)
            except Exception:
                pass
        else:
            try:
                self.unbind_all("<MouseWheel>")
                self.unbind_all("<Button-4>")
                self.unbind_all("<Button-5>")
            except Exception:
                pass

    def _on_mousewheel(self, event):
        try:
            self.hosts_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        except Exception:
            pass

    def _on_mousewheel_linux(self, event):
        try:
            if event.num == 4:
                self.hosts_canvas.yview_scroll(-3, "units")
            elif event.num == 5:
                self.hosts_canvas.yview_scroll(3, "units")
        except Exception:
            pass

    # ---- add host
    def open_add_host(self):
        AddHostDialog(self, self._add_host_from_popup, self.palette)

    def _add_host_from_popup(self, host, port):
        if not host:
            messagebox.showerror("Missing host", "Please enter a host or IP.")
            return
        cfg = MonitorConfig(host=host, port=int(port or 22), interval=self.default_interval, timeout=self.default_timeout)
        self._create_panel(cfg)
        self._save_configs()
        self._sort_panels()

    # ---- host management
    def _create_panel(self, cfg: MonitorConfig):
        panel = HostPanel(self.hosts_frame, self.images, cfg, on_remove=self._remove_panel, get_defaults=lambda: (self.default_interval, self.default_timeout), palette=self.palette, ultra_var=self.ultra_var, on_update=self._on_panel_update)
        panel.pack(fill="x", pady=8)
        self.panels.append(panel)

    def _remove_panel(self, panel: 'HostPanel'):
        try:
            self.panels.remove(panel)
        except ValueError:
            pass
        self._save_configs()

    def _start_stop_all(self):
        any_running = any(p.worker and p.worker.is_alive() for p in self.panels)
        for p in self.panels:
            if any_running:
                p.stop()
            else:
                p.start()

    # ---- sorting
    def _load_sort(self) -> str:
        try:
            with open(CFG_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return str(raw.get('sort_by', 'status'))
        except Exception:
            return 'status'

    def _load_autosort(self) -> bool:
        try:
            with open(CFG_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return bool(raw.get('auto_sort', True))
        except Exception:
            return True

    def _load_ultra(self) -> bool:
        try:
            with open(CFG_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return bool(raw.get('ultra', False))
        except Exception:
            return False

    def _sort_panels(self):
        key = self.sort_by.get()
        if key == 'latency':
            def k(p):
                return (p.last_latency(), p.get_cfg().host.lower())
        elif key == 'host':
            def k(p):
                return p.get_cfg().host.lower()
        else:  # status
            def k(p):
                # ONLINE primero (True > False), luego latencia
                return (not p.last_ok(), p.last_latency(), p.get_cfg().host.lower())
        ordered = sorted(self.panels, key=k)
        for p in ordered:
            p.pack_forget()
        for p in ordered:
            p.pack(fill="x", pady=8)
        # ensure internal order matches
        self.panels = ordered
        self._persist_defaults()

    def _on_panel_update(self):
        if bool(self.auto_sort_var.get()):
            self._sort_panels()

    # ---- persistence
    def _load_defaults(self):
        try:
            with open(CFG_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            a = float(raw.get('alpha', 1.0))
            try:
                self.attributes("-alpha", a)
            except Exception:
                pass
            return float(raw.get('default_interval', 2.0)), float(raw.get('default_timeout', 2.0))
        except Exception:
            return 2.0, 2.0

    def _persist_defaults(self):
        try:
            data = {}
            try:
                with open(CFG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
            data['default_interval'] = self.default_interval
            data['default_timeout'] = self.default_timeout
            data['alpha'] = float(self.attributes('-alpha')) if self.attributes('-alpha') else 1.0
            data['theme'] = self.theme_var.get()
            data['sort_by'] = self.sort_by.get()
            data['auto_sort'] = bool(self.auto_sort_var.get())
            data['ultra'] = bool(self.ultra_var.get())
            data['hosts'] = [asdict(p.get_cfg()) for p in getattr(self, 'panels', [])]
            with open(CFG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _load_configs(self) -> List[MonitorConfig]:
        try:
            with open(CFG_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            items = raw.get("hosts", [])
            out = []
            for it in items:
                out.append(MonitorConfig(
                    host=it.get("host", "example.com"),
                    port=int(it.get("port", 22)),
                    interval=float(it.get("interval", 2.0)),
                    timeout=float(it.get("timeout", 2.0)),
                    sound_on_change=bool(it.get("sound_on_change", True)),
                ))
            return out
        except Exception:
            return []

    def _save_configs(self):
        try:
            payload = {"hosts": [asdict(p.get_cfg()) for p in self.panels],
                       "default_interval": self.default_interval,
                       "default_timeout": self.default_timeout,
                       "alpha": float(self.attributes('-alpha')) if self.attributes('-alpha') else 1.0,
                       "theme": self.theme_var.get(),
                       "sort_by": self.sort_by.get(),
                       "auto_sort": bool(self.auto_sort_var.get()),
                       "ultra": bool(self.ultra_var.get())}
            with open(CFG_PATH, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _on_close(self):
        for p in list(getattr(self, 'panels', [])):
            p.stop()
        self._save_configs()
        self.destroy()


# ----------------------------- Entrypoint ---------------------------------- #

def parse_args(argv):
    p = argparse.ArgumentParser(description="Simple SSH reachability monitor")
    p.add_argument("--host", default="example.com", help="Hostname or IP to check")
    p.add_argument("--port", type=int, default=22, help="TCP port (default 22)")
    p.add_argument("--interval", type=float, default=2.0, help="Seconds between checks")
    p.add_argument("--timeout", type=float, default=2.0, help="TCP connect timeout")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    cfg = MonitorConfig(host=args.host, port=args.port, interval=args.interval, timeout=args.timeout)
    app = App(cfg)
    app.mainloop()


if __name__ == "__main__":
    main()
