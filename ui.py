"""
Tkinter user interface.

Left pane  : manual peer entry (IP + port) — no UDP discovery needed.
Right pane : file picker, transfer progress, log.

All cross-thread communication uses queue.Queue because Tkinter is
NOT thread-safe: only the main thread may touch widgets directly.
"""

import json
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import transfer
from config import PEERS_FILE, SAVE_DIR, TCP_PORT
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
        self._load_peers()

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
        self._peer_list.bind("<Button-3>", self._on_peer_right_click)
        self._peer_menu = tk.Menu(self, tearoff=0)
        self._peer_menu.add_command(label="Edit", command=self._edit_selected_peer)
        self._peer_menu.add_command(label="Delete", command=self._remove_peer)

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
        tk.Button(btn_row2, text="Open Received Folder",
                  bg=SURFACE2, fg=FG, font=FONT_BOLD, relief="flat",
                  activebackground=SURFACE2, activeforeground=FG,
                  cursor="hand2", padx=12, pady=6,
                  command=self._open_received_folder).pack(side="left")
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

        self._add_peer_entry(name=name, ip=ip, port=port)
        self._save_peers()

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
        self._save_peers()
        self._append_log(f"Peer removed: {removed['name']}")

    def _on_peer_right_click(self, event: tk.Event) -> None:
        idx = self._peer_list.nearest(event.y)
        if idx < 0 or idx >= len(self._peers):
            return
        self._peer_list.selection_clear(0, "end")
        self._peer_list.selection_set(idx)
        self._peer_list.activate(idx)
        self._peer_menu.tk_popup(event.x_root, event.y_root)
        self._peer_menu.grab_release()

    def _edit_selected_peer(self) -> None:
        sel = self._peer_list.curselection()
        if not sel:
            return
        idx = sel[0]
        peer = self._peers[idx]
        self._open_edit_peer_dialog(idx, peer)

    def _open_edit_peer_dialog(self, idx: int, peer: dict) -> None:
        top = tk.Toplevel(self)
        top.title("Edit Peer")
        top.configure(bg=BG)
        top.transient(self)
        top.grab_set()
        top.resizable(False, False)

        name_var = tk.StringVar(value=str(peer.get("name", "")))
        ip_var = tk.StringVar(value=str(peer.get("ip", "")))
        port_var = tk.StringVar(value=str(peer.get("port", TCP_PORT)))

        form = tk.Frame(top, bg=BG, padx=16, pady=14)
        form.pack(fill="both", expand=True)

        tk.Label(form, text="Label", font=FONT_BODY, bg=BG, fg=FG_DIM).grid(row=0, column=0, sticky="w")
        tk.Entry(form, textvariable=name_var, font=FONT_BODY, bg=SURFACE2, fg=FG,
                 insertbackground=FG, relief="flat", width=28).grid(row=0, column=1, padx=(10, 0), pady=(0, 8))

        tk.Label(form, text="IP Address", font=FONT_BODY, bg=BG, fg=FG_DIM).grid(row=1, column=0, sticky="w")
        tk.Entry(form, textvariable=ip_var, font=FONT_BODY, bg=SURFACE2, fg=FG,
                 insertbackground=FG, relief="flat", width=28).grid(row=1, column=1, padx=(10, 0), pady=(0, 8))

        tk.Label(form, text="Port", font=FONT_BODY, bg=BG, fg=FG_DIM).grid(row=2, column=0, sticky="w")
        tk.Entry(form, textvariable=port_var, font=FONT_BODY, bg=SURFACE2, fg=FG,
                 insertbackground=FG, relief="flat", width=28).grid(row=2, column=1, padx=(10, 0), pady=(0, 8))

        btns = tk.Frame(form, bg=BG)
        btns.grid(row=3, column=0, columnspan=2, sticky="e", pady=(8, 0))

        def _save_edit() -> None:
            ip = ip_var.get().strip()
            name = name_var.get().strip() or ip
            try:
                port = int(port_var.get().strip())
            except ValueError:
                messagebox.showerror("Invalid port", "Port must be a number.", parent=top)
                return
            if not ip:
                messagebox.showerror("No IP", "IP address cannot be empty.", parent=top)
                return

            for i, p in enumerate(self._peers):
                if i != idx and p["ip"] == ip and p["port"] == port:
                    messagebox.showerror("Duplicate peer", f"{ip}:{port} already exists.", parent=top)
                    return

            self._peers[idx] = {"name": name, "ip": ip, "port": port}
            self._peer_list.delete(idx)
            self._peer_list.insert(idx, f"  {name}  —  {ip}:{port}")
            self._peer_list.selection_clear(0, "end")
            self._peer_list.selection_set(idx)
            self._save_peers()
            self._append_log(f"Peer updated: {name} ({ip}:{port})")
            top.destroy()

        tk.Button(btns, text="Cancel", font=FONT_BOLD, bg=SURFACE2, fg=FG_DIM,
                  relief="flat", padx=14, pady=5, command=top.destroy).pack(side="right", padx=(8, 0))
        tk.Button(btns, text="Save", font=FONT_BOLD, bg=ACCENT, fg="white",
                  relief="flat", padx=18, pady=5, command=_save_edit).pack(side="right")

    # ── File management ──────────────────────────────────────────────────────

    def _browse(self) -> None:
        paths = filedialog.askopenfilenames(title="Select files to send")
        if paths:
            self._files = list(paths)
            self._refresh_files_label()

    def _clear_files(self) -> None:
        self._files = []
        self._refresh_files_label()

    def _open_received_folder(self) -> None:
        os.makedirs(self.save_dir, exist_ok=True)
        try:
            os.startfile(self.save_dir)  # type: ignore[attr-defined]
        except AttributeError:
            messagebox.showinfo("Folder path", f"Received files folder:\n{self.save_dir}")
        except OSError as exc:
            messagebox.showerror("Open folder failed", f"Could not open folder:\n{exc}")

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
                sender_tcp_port=self.tcp_port,
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
            sender_ip = str(request_msg.get("sender_ip", "")).strip()
            sender_port = int(request_msg.get("sender_tcp_port") or TCP_PORT)

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
            if sender_ip:
                tk.Label(top, text=f"From: {sender_ip}:{sender_port}",
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
        accepted = bool(result["accepted"])
        if accepted:
            self._auto_add_peer_from_request(request_msg)
        return accepted

    def _auto_add_peer_from_request(self, request_msg: dict) -> None:
        sender_ip = str(request_msg.get("sender_ip", "")).strip()
        if not sender_ip:
            return
        sender_port = int(request_msg.get("sender_tcp_port") or TCP_PORT)
        sender_name = str(request_msg.get("sender_name", "")).strip() or sender_ip

        for p in self._peers:
            if p["ip"] == sender_ip and p["port"] == sender_port:
                return

        self._add_peer_entry(name=sender_name, ip=sender_ip, port=sender_port)
        self._save_peers()
        self._append_log(f"Auto-saved peer: {sender_name} ({sender_ip}:{sender_port})")

    def _add_peer_entry(self, *, name: str, ip: str, port: int) -> None:
        peer = {"name": name, "ip": ip, "port": port}
        self._peers.append(peer)
        self._peer_list.insert("end", f"  {name}  —  {ip}:{port}")

    def _load_peers(self) -> None:
        try:
            if not os.path.isfile(PEERS_FILE):
                return
            with open(PEERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                return

            loaded = 0
            for item in data:
                if not isinstance(item, dict):
                    continue
                ip = str(item.get("ip", "")).strip()
                if not ip:
                    continue
                try:
                    port = int(item.get("port", TCP_PORT))
                except (TypeError, ValueError):
                    continue
                name = str(item.get("name", "")).strip() or ip

                duplicate = any(p["ip"] == ip and p["port"] == port for p in self._peers)
                if duplicate:
                    continue
                self._add_peer_entry(name=name, ip=ip, port=port)
                loaded += 1

            if loaded:
                self._append_log(f"Loaded {loaded} saved peer(s).")
        except (OSError, json.JSONDecodeError):
            self._append_log("Could not load saved peers; starting with empty list.")

    def _save_peers(self) -> None:
        try:
            with open(PEERS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._peers, f, indent=2)
        except OSError as exc:
            self._append_log(f"Failed to save peers: {exc}")

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
