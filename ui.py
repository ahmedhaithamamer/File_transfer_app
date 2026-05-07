"""
Tkinter user interface.

Left pane  : manual peer entry (IP + port) — no UDP discovery needed.
Right pane : file picker, transfer progress, log.

All cross-thread communication uses queue.Queue because Tkinter is
NOT thread-safe: only the main thread may touch widgets directly.
"""

import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import transfer
from config import SAVE_DIR, TCP_PORT
from utils import Device, format_size, format_speed


# ── Theme ────────────────────────────────────────────────────────────────────
BG, SURFACE, SURFACE2 = "#181825", "#1e1e2e", "#2a2a3e"
ACCENT, ACCENT_HV     = "#7c6af7", "#5a4fcf"
FG, FG_DIM            = "#cdd6f4", "#9399b2"
OK, ERR               = "#a6e3a1", "#f38ba8"

FONT_TITLE = ("Segoe UI", 14, "bold")
FONT_BOLD  = ("Segoe UI", 10, "bold")
FONT_BODY  = ("Segoe UI", 10)
FONT_MONO  = ("Consolas",  9)


class LanShareApp(tk.Tk):
    def __init__(
        self,
        device: Device,
        save_dir: str = SAVE_DIR,
        tcp_port: int = TCP_PORT,
    ):
        super().__init__()
        self.device   = device
        self.save_dir = save_dir
        self.tcp_port = tcp_port
        self.title(f"LAN Share — {device.name}")
        self.geometry("900x600")
        self.configure(bg=BG)
        self.minsize(820, 540)

        self._files:   list[str]            = []
        self._peers:   list[dict]           = []   # {"name", "ip", "port"}
        self._q: "queue.Queue[tuple]"       = queue.Queue()

        self._build_ui()

        # ── Start TCP receive server ─────────────────────────────────────────
        self._stop_server = threading.Event()
        threading.Thread(
            target=transfer.serve_forever,
            kwargs=dict(
                port=self.tcp_port,
                accept_callback=self._on_accept_request,
                on_log=      lambda m:       self._q.put(("log",      m)),
                on_progress= lambda d, t, s: self._q.put(("progress", d, t, s)),
                on_file_saved=lambda p:      self._q.put(("saved",    p)),
                save_dir=save_dir,
                stop_flag=self._stop_server,
            ),
            daemon=True, name="tcp-server",
        ).start()

        self._poll_queue()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── UI layout ────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # ── Title bar ────────────────────────────────────────────────────────
        bar = tk.Frame(self, bg=ACCENT, padx=14, pady=10)
        bar.pack(fill="x")
        tk.Label(bar, text="LAN Share",
                 font=FONT_TITLE, bg=ACCENT, fg="white").pack(side="left")
        tk.Label(bar, text=f"  {self.device.name}  •  receiving on TCP {self.tcp_port}",
                 font=FONT_BODY, bg=ACCENT, fg="#dcd9ff").pack(side="left")

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=12, pady=12)

        # ── Left pane: manual peer management ────────────────────────────────
        left = tk.Frame(body, bg=SURFACE, width=260)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        tk.Label(left, text="SEND TO", font=FONT_BOLD,
                 bg=SURFACE, fg=FG_DIM).pack(anchor="w", padx=12, pady=(12, 4))

        # IP entry
        ip_row = tk.Frame(left, bg=SURFACE)
        ip_row.pack(fill="x", padx=12, pady=(0, 4))
        tk.Label(ip_row, text="IP Address", font=FONT_BODY,
                 bg=SURFACE, fg=FG_DIM, width=9, anchor="w").pack(side="left")
        self._ip_var = tk.StringVar()
        tk.Entry(ip_row, textvariable=self._ip_var, font=FONT_BODY,
                 bg=SURFACE2, fg=FG, insertbackground=FG,
                 relief="flat").pack(side="left", fill="x", expand=True, padx=(6, 0))

        # Port entry
        port_row = tk.Frame(left, bg=SURFACE)
        port_row.pack(fill="x", padx=12, pady=(0, 4))
        tk.Label(port_row, text="Port", font=FONT_BODY,
                 bg=SURFACE, fg=FG_DIM, width=9, anchor="w").pack(side="left")
        self._port_var = tk.StringVar(value=str(TCP_PORT))
        tk.Entry(port_row, textvariable=self._port_var, font=FONT_BODY,
                 bg=SURFACE2, fg=FG, insertbackground=FG,
                 relief="flat").pack(side="left", fill="x", expand=True, padx=(6, 0))

        # Name entry (optional label shown in list)
        name_row = tk.Frame(left, bg=SURFACE)
        name_row.pack(fill="x", padx=12, pady=(0, 8))
        tk.Label(name_row, text="Label", font=FONT_BODY,
                 bg=SURFACE, fg=FG_DIM, width=9, anchor="w").pack(side="left")
        self._peer_name_var = tk.StringVar()
        tk.Entry(name_row, textvariable=self._peer_name_var, font=FONT_BODY,
                 bg=SURFACE2, fg=FG, insertbackground=FG,
                 relief="flat").pack(side="left", fill="x", expand=True, padx=(6, 0))

        # Add + Remove buttons
        btn_row = tk.Frame(left, bg=SURFACE)
        btn_row.pack(fill="x", padx=12, pady=(0, 8))
        tk.Button(btn_row, text="Add Peer", font=FONT_BOLD,
                  bg=ACCENT, fg="white", activebackground=ACCENT_HV,
                  activeforeground="white", relief="flat", cursor="hand2",
                  padx=10, pady=5, command=self._add_peer).pack(side="left")
        tk.Button(btn_row, text="Remove", font=FONT_BOLD,
                  bg=SURFACE2, fg=FG_DIM, activebackground=SURFACE2,
                  activeforeground=FG, relief="flat", cursor="hand2",
                  padx=10, pady=5, command=self._remove_peer).pack(side="right")

        tk.Frame(left, bg=SURFACE2, height=1).pack(fill="x", padx=12, pady=(0, 6))
        tk.Label(left, text="PEERS", font=FONT_BOLD,
                 bg=SURFACE, fg=FG_DIM).pack(anchor="w", padx=12, pady=(0, 4))

        # Peer listbox
        self._peer_list = tk.Listbox(
            left, bg=SURFACE2, fg=FG, font=FONT_BODY,
            selectbackground=ACCENT, selectforeground="white",
            relief="flat", activestyle="none", highlightthickness=0,
        )
        self._peer_list.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # ── Right pane ───────────────────────────────────────────────────────
        right = tk.Frame(body, bg=BG)
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))

        # File picker
        files_frame = tk.Frame(right, bg=SURFACE, padx=14, pady=12)
        files_frame.pack(fill="x")

        tk.Label(files_frame, text="Files to Send", font=FONT_BOLD,
                 bg=SURFACE, fg=FG).pack(anchor="w")
        self._files_var = tk.StringVar(value="(no files selected)")
        tk.Label(files_frame, textvariable=self._files_var, font=FONT_BODY,
                 bg=SURFACE, fg=FG_DIM, anchor="w",
                 wraplength=580, justify="left").pack(fill="x", pady=(4, 8))

        btn_row2 = tk.Frame(files_frame, bg=SURFACE)
        btn_row2.pack(fill="x")
        tk.Button(btn_row2, text="Browse Files…",
                  bg=SURFACE2, fg=FG, font=FONT_BOLD, relief="flat",
                  activebackground=SURFACE2, activeforeground=FG,
                  cursor="hand2", padx=12, pady=6,
                  command=self._browse).pack(side="left")
        tk.Button(btn_row2, text="Clear",
                  bg=SURFACE2, fg=FG, font=FONT_BOLD, relief="flat",
                  activebackground=SURFACE2, activeforeground=FG,
                  cursor="hand2", padx=12, pady=6,
                  command=self._clear_files).pack(side="left", padx=8)
        self._send_btn = tk.Button(btn_row2, text="Send",
                  bg=ACCENT, fg="white", font=FONT_BOLD, relief="flat",
                  activebackground=ACCENT_HV, activeforeground="white",
                  cursor="hand2", padx=18, pady=6,
                  command=self._send_clicked)
        self._send_btn.pack(side="right")

        # Progress bar
        prog_frame = tk.Frame(right, bg=SURFACE, padx=14, pady=12)
        prog_frame.pack(fill="x", pady=(12, 0))

        tk.Label(prog_frame, text="Transfer Progress", font=FONT_BOLD,
                 bg=SURFACE, fg=FG).pack(anchor="w")

        style = ttk.Style(); style.theme_use("clam")
        style.configure("Horizontal.TProgressbar",
                        troughcolor=SURFACE2, background=ACCENT,
                        bordercolor=SURFACE2, lightcolor=ACCENT, darkcolor=ACCENT)
        self._pb = ttk.Progressbar(prog_frame, maximum=100)
        self._pb.pack(fill="x", pady=(8, 4))

        stats = tk.Frame(prog_frame, bg=SURFACE)
        stats.pack(fill="x")
        self._pct_var   = tk.StringVar(value="0.0 %")
        self._speed_var = tk.StringVar(value="")
        tk.Label(stats, textvariable=self._pct_var,   font=FONT_BOLD,
                 bg=SURFACE, fg=ACCENT).pack(side="left")
        tk.Label(stats, textvariable=self._speed_var, font=FONT_BODY,
                 bg=SURFACE, fg=FG_DIM).pack(side="right")

        # Log
        log_frame = tk.Frame(right, bg=SURFACE, padx=14, pady=12)
        log_frame.pack(fill="both", expand=True, pady=(12, 0))

        tk.Label(log_frame, text="Log", font=FONT_BOLD,
                 bg=SURFACE, fg=FG).pack(anchor="w")
        self._log = tk.Text(log_frame, height=10, font=FONT_MONO,
                            bg="#13131f", fg=FG, relief="flat",
                            state="disabled", wrap="word", padx=8, pady=6)
        self._log.pack(fill="both", expand=True, pady=(8, 0))

    # ── Peer management ──────────────────────────────────────────────────────

    def _add_peer(self) -> None:
        ip   = self._ip_var.get().strip()
        name = self._peer_name_var.get().strip() or ip

        try:
            port = int(self._port_var.get().strip())
        except ValueError:
            messagebox.showerror("Invalid port", "Port must be a number.")
            return

        if not ip:
            messagebox.showerror("No IP", "Enter the other device's IP address.")
            return

        # Avoid duplicates
        for p in self._peers:
            if p["ip"] == ip and p["port"] == port:
                messagebox.showinfo("Already added", f"{ip}:{port} is already in the list.")
                return

        peer = {"name": name, "ip": ip, "port": port}
        self._peers.append(peer)
        self._peer_list.insert("end", f"  {name}  —  {ip}:{port}")

        # Clear inputs
        self._ip_var.set("")
        self._peer_name_var.set("")
        self._append_log(f"Peer added: {name} ({ip}:{port})")

    def _remove_peer(self) -> None:
        sel = self._peer_list.curselection()
        if not sel:
            return
        idx = sel[0]
        removed = self._peers.pop(idx)
        self._peer_list.delete(idx)
        self._append_log(f"Peer removed: {removed['name']}")

    # ── File management ──────────────────────────────────────────────────────

    def _browse(self) -> None:
        paths = filedialog.askopenfilenames(title="Select files to send")
        if paths:
            self._files = list(paths)
            self._refresh_files_label()

    def _clear_files(self) -> None:
        self._files = []
        self._refresh_files_label()

    def _refresh_files_label(self) -> None:
        if not self._files:
            self._files_var.set("(no files selected)"); return
        names = ", ".join(os.path.basename(p) for p in self._files[:3])
        if len(self._files) > 3:
            names += f", … (+{len(self._files) - 3} more)"
        total = sum(os.path.getsize(p) for p in self._files if os.path.isfile(p))
        self._files_var.set(f"{len(self._files)} file(s) — {format_size(total)}\n{names}")

    # ── Send ─────────────────────────────────────────────────────────────────

    def _send_clicked(self) -> None:
        sel = self._peer_list.curselection()
        if not sel:
            messagebox.showinfo("Select a peer",
                                "Add a peer on the left and select it first.")
            return
        if not self._files:
            messagebox.showinfo("No files", "Click Browse Files first.")
            return

        peer  = self._peers[sel[0]]
        files = list(self._files)
        self._clear_files()
        self._send_btn.configure(state="disabled")

        def _runner() -> None:
            ok = transfer.send_files(
                host=peer["ip"], port=peer["port"],
                filepaths=files,
                sender_name=self.device.name,
                sender_id=self.device.device_id,
                on_log=      lambda m:       self._q.put(("log",      m)),
                on_progress= lambda d, t, s: self._q.put(("progress", d, t, s)),
            )
            self._q.put(("send_done", ok))

        threading.Thread(target=_runner, daemon=True, name="send").start()

    # ── Incoming transfer accept dialog ──────────────────────────────────────

    def _on_accept_request(self, request_msg: dict) -> bool:
        """Show accept/reject dialog from the Tk main thread; block until decided."""
        decided = threading.Event()
        result  = {"accepted": False}

        def _open() -> None:
            files  = request_msg.get("manifest", [])
            sender = request_msg.get("sender_name", "Unknown")
            total  = int(request_msg.get("total_size", 0))

            top = tk.Toplevel(self)
            top.configure(bg=BG)
            top.title("Incoming Files")
            top.transient(self)
            top.grab_set()
            top.protocol("WM_DELETE_WINDOW", lambda: _set(False))

            tk.Label(top, text=f"{sender} wants to send you files",
                     font=FONT_BOLD, bg=BG, fg=FG,
                     padx=20, pady=14).pack(anchor="w")
            tk.Label(top, text=f"{len(files)} file(s)  •  {format_size(total)}",
                     font=FONT_BODY, bg=BG, fg=FG_DIM, padx=20).pack(anchor="w")

            list_frame = tk.Frame(top, bg=SURFACE2)
            list_frame.pack(fill="x", padx=20, pady=12)
            for entry in files[:10]:
                tk.Label(list_frame,
                         text=f"  •  {entry.get('name','?')}   "
                              f"({format_size(entry.get('size', 0))})",
                         font=FONT_BODY, bg=SURFACE2, fg=FG,
                         anchor="w").pack(fill="x")
            if len(files) > 10:
                tk.Label(list_frame,
                         text=f"  …and {len(files) - 10} more",
                         font=FONT_BODY, bg=SURFACE2, fg=FG_DIM,
                         anchor="w").pack(fill="x")

            def _set(v: bool) -> None:
                result["accepted"] = v
                decided.set()
                top.destroy()

            btns = tk.Frame(top, bg=BG)
            btns.pack(fill="x", padx=20, pady=14)
            tk.Button(btns, text="Reject", font=FONT_BOLD,
                      bg=SURFACE2, fg=ERR, relief="flat", padx=18, pady=6,
                      command=lambda: _set(False)).pack(side="right", padx=(8, 0))
            tk.Button(btns, text="Accept", font=FONT_BOLD,
                      bg=ACCENT, fg="white", relief="flat", padx=22, pady=6,
                      command=lambda: _set(True)).pack(side="right")

        self.after(0, _open)
        decided.wait(timeout=120.0)
        return bool(result["accepted"])

    # ── Queue polling ────────────────────────────────────────────────────────

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, *args = self._q.get_nowait()
                if   kind == "log":       self._append_log(args[0])
                elif kind == "progress":  self._update_progress(*args)
                elif kind == "saved":     self._append_log(f"Saved: {args[0]}")
                elif kind == "send_done": self._send_btn.configure(state="normal")
        except queue.Empty:
            pass
        self.after(50, self._poll_queue)

    def _update_progress(self, done: int, total: int, speed: float) -> None:
        pct = (done / total * 100) if total else 0.0
        self._pb["value"] = pct
        self._pct_var.set(f"{pct:.1f} %  —  {format_size(done)} / {format_size(total)}")
        self._speed_var.set(format_speed(speed))

    def _append_log(self, msg: str) -> None:
        self._log.configure(state="normal")
        self._log.insert("end", f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        self._log.see("end")
        self._log.configure(state="disabled")

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def _on_close(self) -> None:
        try:
            self._stop_server.set()
        finally:
            self.destroy()
