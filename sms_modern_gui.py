import os
import sys
import time
import threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import customtkinter as ctk
import keyboard
import sms_licensing
import sms_updater

from sms_core import (
    get_checked_patients,
    uncheck_patient,
    format_sms_message,
    extract_mobiles,
    load_template_config,
    save_template_config,
    get_excel_status,
    configure_checkbox_links,
    switch_to_default_desktop,
    DEFAULT_TEMPLATE
)
from sms_playwright_sender import (
    fill_moding_and_send,
    find_recent_live_attempt,
    is_browser_ready,
    start_moding_browser,
)

# Configure CustomTkinter Appearance
ctk.set_appearance_mode("Dark") # Default to modern dark mode
ctk.set_default_color_theme("blue")

class ModernSMSApp(ctk.CTk):
    def __init__(self):
        switch_to_default_desktop()
        super().__init__()

        self.title("一安藥局新泰店 - 簡訊自動發送管理系統")
        self.geometry("1060, 840")
        self.minsize(920, 680)

        # State Variables
        self.checked_patients = []
        self.is_running = False
        self.stop_requested = False

        # Build UI
        self.create_header()
        self.create_template_card()
        self.create_patient_list_card()
        self.create_control_card()
        self.create_log_card()

        # Keyboard ESC Emergency Stop Binding
        try:
            keyboard.add_hotkey('esc', self.on_emergency_stop)
        except Exception:
            pass
        self.bind_all('<Escape>', lambda e: self.on_emergency_stop())

        # Initial Status Check & Message
        self.after(300, self.initial_status_check)
        if sms_licensing.PACKAGED:
            self.after(500, self.ensure_activation)

    def ensure_activation(self):
        """Require a fresh server check before accessing patient data or sending."""
        try:
            sms_licensing.validate()
            return True
        except sms_licensing.LicenseError as exc:
            self.log(f"授權檢查：{exc}", "WARN")
        choice = messagebox.askyesnocancel(
            "安裝授權",
            "請先啟用這台電腦。\n\n「是」使用分店管理密碼；「否」向管理者申請六位數驗證碼；「取消」稍後再試。",
        )
        if choice is None:
            return False
        store = simpledialog.askstring("安裝授權", "請輸入藥局／分店名稱：", parent=self)
        if not store:
            return False
        try:
            if choice:
                password = simpledialog.askstring("安裝授權", "請輸入分店管理密碼：", show="*", parent=self)
                if not password:
                    return False
                sms_licensing.verify_password(store, password)
            else:
                result = sms_licensing.request_code(store)
                messagebox.showinfo("驗證碼已申請", result.get("message", "請向管理者索取驗證碼。"))
                code = simpledialog.askstring("安裝授權", "請輸入六位數驗證碼：", parent=self)
                if not code:
                    return False
                sms_licensing.verify_code(code)
            sms_licensing.validate()
            self.log("這台電腦已完成安裝授權。", "SUCCESS")
            return True
        except sms_licensing.LicenseError as exc:
            messagebox.showerror("授權失敗", str(exc))
            return False

    def initial_status_check(self):
        self.log("系統啟動完成！", "SUCCESS")
        self.log("安全防護生效中：目前處於【🛡️ 模擬測試模式】，僅填入網頁供檢視，絕對不按發送。", "INFO")
        self.refresh_system_status()

    def refresh_system_status(self):
        # Check Excel and explicitly expose read-only mode.
        try:
            excel_status = get_excel_status()
            if excel_status['connected'] and excel_status['readonly']:
                self.badge_excel.configure(text="Excel: 🟠 唯讀", text_color="#F59E0B")
            elif excel_status['connected']:
                self.badge_excel.configure(text="Excel: 🟢 已連線", text_color="#10B981")
            else:
                self.badge_excel.configure(text="Excel: 🔴 未連線", text_color="#EF4444")
        except Exception:
            self.badge_excel.configure(text="Excel: 🔴 未連線", text_color="#EF4444")

        # Check the dedicated Playwright-controlled Chrome (no extension).
        try:
            if is_browser_ready():
                self.badge_moding.configure(text="摩叮瀏覽器: 🟢 已連線", text_color="#10B981")
            else:
                self.badge_moding.configure(text="摩叮瀏覽器: 🟠 未開啟", text_color="#F59E0B")
        except Exception:
            self.badge_moding.configure(text="摩叮瀏覽器: 🔴 錯誤", text_color="#EF4444")

        # Auto refresh every 2 seconds
        self.after(2000, self.refresh_system_status)

    def open_moding_browser(self):
        if sms_licensing.PACKAGED and not self.ensure_activation():
            return
        ok, reason = start_moding_browser(lambda msg: self.log(msg, "INFO"))
        if ok:
            self.log(reason, "SUCCESS")
            messagebox.showinfo(
                "摩叮專用瀏覽器",
                "已開啟不依賴 extension 的摩叮專用 Chrome。\n\n"
                "首次使用請在該視窗手動登入；之後登入狀態會保留。"
            )
        else:
            self.log(reason, "WARN")
            messagebox.showerror("無法開啟摩叮瀏覽器", reason)

    def repair_excel_checkboxes(self):
        if sms_licensing.PACKAGED and not self.ensure_activation():
            return
        if not messagebox.askyesno(
            "修復 Excel 核取方塊",
            "程式會先建立備份，再將每個核取方塊連結到隱藏的輔助欄位。\n\n"
            "這能避免核取方塊位移後抓錯病患。是否繼續？"
        ):
            return
        try:
            result = configure_checkbox_links()
            self.log(
                f"已修復 {result['count']} 個核取方塊；備份：{result['backup_path']}",
                "SUCCESS"
            )
            messagebox.showinfo(
                "修復完成",
                f"已設定 {result['count']} 個核取方塊並儲存。\n\n備份位置：\n{result['backup_path']}"
            )
        except Exception as exc:
            self.log(f"核取方塊修復未執行：{exc}", "WARN")
            messagebox.showerror("無法修復", str(exc))

    # ================= UI CREATION =================
    def create_header(self):
        header_frame = ctk.CTkFrame(self, corner_radius=12, fg_color="#1E293B", height=65)
        header_frame.pack(fill=tk.X, padx=14, pady=(12, 6))

        # Title and Subtitle
        title_box = ctk.CTkFrame(header_frame, fg_color="transparent")
        title_box.pack(side=tk.LEFT, padx=15, pady=8)

        lbl_logo = ctk.CTkLabel(
            title_box, 
            text="🏥 一安藥局新泰店 ｜ 處方箋簡訊自動化系統", 
            font=ctk.CTkFont(family="Microsoft JhengHei", size=17, weight="bold"),
            text_color="#F8FAFC"
        )
        lbl_logo.pack(anchor=tk.W)

        lbl_desc = ctk.CTkLabel(
            title_box, 
            text="即時讀取 Excel 勾選 ➜ 自動過濾市話 ➜ 代入病患姓名 ➜ 自動填入摩叮網頁 ➜ 發送後自動取消勾選", 
            font=ctk.CTkFont(family="Microsoft JhengHei", size=11),
            text_color="#94A3B8"
        )
        lbl_desc.pack(anchor=tk.W)

        # Status Badges on the Right
        status_box = ctk.CTkFrame(header_frame, fg_color="transparent")
        status_box.pack(side=tk.RIGHT, padx=15, pady=10)

        self.badge_excel = ctk.CTkLabel(
            status_box, 
            text="Excel: 檢測中...", 
            font=ctk.CTkFont(family="Microsoft JhengHei", size=11, weight="bold"),
            fg_color="#0F172A",
            corner_radius=8,
            padx=10, pady=4
        )
        self.badge_excel.pack(side=tk.LEFT, padx=5)

        self.badge_moding = ctk.CTkLabel(
            status_box, 
            text="摩叮助手: 檢測中...", 
            font=ctk.CTkFont(family="Microsoft JhengHei", size=11, weight="bold"),
            fg_color="#0F172A",
            corner_radius=8,
            padx=10, pady=4
        )
        self.badge_moding.pack(side=tk.LEFT, padx=5)

        btn_browser = ctk.CTkButton(
            status_box, 
            text="🌐 開啟摩叮", 
            height=28, 
            font=ctk.CTkFont(family="Microsoft JhengHei", size=11, weight="bold"),
            fg_color="#4F46E5", hover_color="#4338CA",
            corner_radius=8,
            command=self.open_moding_browser
        )
        btn_browser.pack(side=tk.LEFT, padx=5)

        btn_theme = ctk.CTkButton(
            status_box, 
            text="🌓", 
            width=36, height=28, 
            corner_radius=8,
            fg_color="#334155", hover_color="#475569",
            command=self.toggle_theme
        )
        btn_theme.pack(side=tk.LEFT, padx=5)

    def toggle_theme(self):
        curr = ctk.get_appearance_mode()
        new_mode = "Light" if curr == "Dark" else "Dark"
        ctk.set_appearance_mode(new_mode)
        self.apply_treeview_theme(new_mode)

    def create_template_card(self):
        tpl_card = ctk.CTkFrame(self, corner_radius=12, fg_color="#1E293B")
        tpl_card.pack(fill=tk.X, padx=14, pady=6)

        # Card Title Bar
        title_bar = ctk.CTkFrame(tpl_card, fg_color="transparent")
        title_bar.pack(fill=tk.X, padx=14, pady=(10, 4))

        lbl_card_title = ctk.CTkLabel(
            title_bar, 
            text="📝 簡訊內容範本設定", 
            font=ctk.CTkFont(family="Microsoft JhengHei", size=13, weight="bold"),
            text_color="#38BDF8"
        )
        lbl_card_title.pack(side=tk.LEFT)

        lbl_hint = ctk.CTkLabel(
            title_bar, 
            text="（支援 {姓名} 或 XXX 自動代入病患姓名，修改後請點「儲存範本」）", 
            font=ctk.CTkFont(family="Microsoft JhengHei", size=11),
            text_color="#94A3B8"
        )
        lbl_hint.pack(side=tk.LEFT, padx=8)

        btn_reset = ctk.CTkButton(
            title_bar, 
            text="🔄 恢復預設", 
            width=90, height=26, 
            font=ctk.CTkFont(family="Microsoft JhengHei", size=11),
            fg_color="#475569", hover_color="#64748B",
            corner_radius=6,
            command=self.reset_template
        )
        btn_reset.pack(side=tk.RIGHT, padx=(6, 0))

        btn_save = ctk.CTkButton(
            title_bar, 
            text="💾 儲存並套用範本", 
            width=120, height=26, 
            font=ctk.CTkFont(family="Microsoft JhengHei", size=11, weight="bold"),
            fg_color="#0EA5E9", hover_color="#0284C7",
            corner_radius=6,
            command=self.save_template
        )
        btn_save.pack(side=tk.RIGHT)

        # Text Area
        self.txt_template = ctk.CTkTextbox(
            tpl_card, 
            height=68, 
            corner_radius=8, 
            font=ctk.CTkFont(family="Microsoft JhengHei", size=12),
            fg_color="#0F172A",
            border_width=1,
            border_color="#334155"
        )
        saved_tpl = load_template_config()
        self.txt_template.insert("1.0", saved_tpl)
        self.txt_template.pack(fill=tk.X, padx=14, pady=(2, 12))
        self.txt_template.bind('<KeyRelease>', lambda e: self.update_table_previews())

    def create_patient_list_card(self):
        list_card = ctk.CTkFrame(self, corner_radius=12, fg_color="#1E293B")
        list_card.pack(fill=tk.BOTH, expand=True, padx=14, pady=6)

        # Card Title Bar
        title_bar = ctk.CTkFrame(list_card, fg_color="transparent")
        title_bar.pack(fill=tk.X, padx=14, pady=(10, 6))

        lbl_card_title = ctk.CTkLabel(
            title_bar, 
            text="👥 待發送病患名單", 
            font=ctk.CTkFont(family="Microsoft JhengHei", size=13, weight="bold"),
            text_color="#38BDF8"
        )
        lbl_card_title.pack(side=tk.LEFT)

        btn_scan = ctk.CTkButton(
            title_bar, 
            text="🔍 掃描 Excel 勾選資料", 
            font=ctk.CTkFont(family="Microsoft JhengHei", size=12, weight="bold"),
            fg_color="#2563EB", hover_color="#1D4ED8",
            corner_radius=8, height=32, width=160,
            command=self.scan_excel
        )
        btn_scan.pack(side=tk.LEFT, padx=12)

        btn_repair = ctk.CTkButton(
            title_bar,
            text="🔧 修復核取方塊",
            font=ctk.CTkFont(family="Microsoft JhengHei", size=11),
            fg_color="#475569", hover_color="#64748B",
            corner_radius=8, height=30, width=115,
            command=self.repair_excel_checkboxes
        )
        btn_repair.pack(side=tk.LEFT, padx=(0, 8))

        # Stats Badges
        self.lbl_stats_checked = ctk.CTkLabel(
            title_bar, 
            text="已勾選: 0 人", 
            font=ctk.CTkFont(family="Microsoft JhengHei", size=11, weight="bold"),
            fg_color="#0F172A", corner_radius=6, padx=8, pady=3, text_color="#E2E8F0"
        )
        self.lbl_stats_checked.pack(side=tk.LEFT, padx=4)

        self.lbl_stats_mobiles = ctk.CTkLabel(
            title_bar, 
            text="有效手機: 0 支", 
            font=ctk.CTkFont(family="Microsoft JhengHei", size=11, weight="bold"),
            fg_color="#0F172A", corner_radius=6, padx=8, pady=3, text_color="#10B981"
        )
        self.lbl_stats_mobiles.pack(side=tk.LEFT, padx=4)

        self.lbl_stats_landlines = ctk.CTkLabel(
            title_bar, 
            text="已過濾市話: 0 筆", 
            font=ctk.CTkFont(family="Microsoft JhengHei", size=11),
            fg_color="#0F172A", corner_radius=6, padx=8, pady=3, text_color="#94A3B8"
        )
        self.lbl_stats_landlines.pack(side=tk.LEFT, padx=4)

        # Modern Treeview Table inside Frame
        table_container = tk.Frame(list_card, bg="#0F172A")
        table_container.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 12))

        columns = ('row', 'name', 'mobiles', 'landlines', 'preview')
        self.tree = ttk.Treeview(table_container, columns=columns, show='headings', selectmode='browse')

        self.tree.heading('row', text='Excel 列')
        self.tree.heading('name', text='病患姓名')
        self.tree.heading('mobiles', text='行動電話 (將發送)')
        self.tree.heading('landlines', text='已過濾市話')
        self.tree.heading('preview', text='個人化簡訊內容預覽')

        self.tree.column('row', width=75, anchor=tk.CENTER)
        self.tree.column('name', width=130, anchor=tk.W)
        self.tree.column('mobiles', width=180, anchor=tk.W)
        self.tree.column('landlines', width=130, anchor=tk.W)
        self.tree.column('preview', width=480, anchor=tk.W)

        scrollbar = ttk.Scrollbar(table_container, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.apply_treeview_theme("Dark")

    def apply_treeview_theme(self, mode):
        style = ttk.Style(self)
        style.theme_use('clam')
        if mode == "Dark":
            bg_col = "#0F172A"
            fg_col = "#F8FAFC"
            head_bg = "#1E293B"
            head_fg = "#94A3B8"
            sel_bg = "#2563EB"
        else:
            bg_col = "#FFFFFF"
            fg_col = "#0F172A"
            head_bg = "#F1F5F9"
            head_fg = "#475569"
            sel_bg = "#3B82F6"

        style.configure('Treeview', background=bg_col, foreground=fg_col, fieldbackground=bg_col, rowheight=28, font=('Microsoft JhengHei', 9))
        style.configure('Treeview.Heading', background=head_bg, foreground=head_fg, font=('Microsoft JhengHei', 10, 'bold'), borderwidth=0)
        style.map('Treeview', background=[('selected', sel_bg)], foreground=[('selected', '#FFFFFF')])

    def create_control_card(self):
        ctrl_card = ctk.CTkFrame(self, corner_radius=12, fg_color="#1E293B")
        ctrl_card.pack(fill=tk.X, padx=14, pady=6)

        inner_box = ctk.CTkFrame(ctrl_card, fg_color="transparent")
        inner_box.pack(fill=tk.X, padx=14, pady=10)

        # Left: Dry Run Switch
        self.var_dry_run = tk.BooleanVar(value=True)
        self.switch_dry_run = ctk.CTkSwitch(
            inner_box, 
            text="🛡️ 模擬測試模式（只填寫預覽，絕對不按發送）",
            variable=self.var_dry_run,
            font=ctk.CTkFont(family="Microsoft JhengHei", size=12, weight="bold"),
            progress_color="#10B981",
            command=self.on_dry_run_toggle
        )
        self.switch_dry_run.pack(side=tk.LEFT, padx=(5, 20))

        # Action Button: Start Process
        self.btn_start = ctk.CTkButton(
            inner_box, 
            text="🚀 開始執行發送", 
            font=ctk.CTkFont(family="Microsoft JhengHei", size=13, weight="bold"),
            fg_color="#059669", hover_color="#047857",
            corner_radius=8, height=38, width=160,
            command=self.start_process
        )
        self.btn_start.pack(side=tk.LEFT, padx=8)

        # Big Emergency Stop Button (Right)
        self.btn_stop = ctk.CTkButton(
            inner_box, 
            text="🚨 緊急停止 (ESC)", 
            font=ctk.CTkFont(family="Microsoft JhengHei", size=13, weight="bold"),
            fg_color="#DC2626", hover_color="#B91C1C",
            corner_radius=8, height=38, width=150,
            command=self.on_emergency_stop
        )
        self.btn_stop.pack(side=tk.RIGHT, padx=5)

        # Center Progress Bar
        self.progress_bar = ctk.CTkProgressBar(inner_box, orientation="horizontal", mode="determinate", height=14, corner_radius=6)
        self.progress_bar.set(0)
        self.progress_bar.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=20)

    def create_log_card(self):
        log_card = ctk.CTkFrame(self, corner_radius=12, fg_color="#1E293B")
        log_card.pack(fill=tk.X, padx=14, pady=(6, 12))

        title_bar = ctk.CTkFrame(log_card, fg_color="transparent")
        title_bar.pack(fill=tk.X, padx=14, pady=(8, 4))

        lbl_log_title = ctk.CTkLabel(
            title_bar, 
            text="📊 系統即時執行日誌", 
            font=ctk.CTkFont(family="Microsoft JhengHei", size=12, weight="bold"),
            text_color="#94A3B8"
        )
        lbl_log_title.pack(side=tk.LEFT)

        ctk.CTkButton(
            title_bar, text="檢查更新", width=88, height=24,
            command=self.check_for_updates,
        ).pack(side=tk.RIGHT, padx=6)

        btn_clear_log = ctk.CTkButton(
            title_bar, 
            text="清空日誌", 
            width=65, height=22, 
            font=ctk.CTkFont(family="Microsoft JhengHei", size=10),
            fg_color="#334155", hover_color="#475569",
            corner_radius=4,
            command=self.clear_log
        )
        btn_clear_log.pack(side=tk.RIGHT)

        self.txt_log = ctk.CTkTextbox(
            log_card, 
            height=100, 
            corner_radius=8,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color="#0F172A",
            text_color="#E2E8F0",
            border_width=1,
            border_color="#334155"
        )
        self.txt_log.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 10))

    # ================= LOGIC & ACTIONS =================
    def log(self, message, level="INFO"):
        if threading.current_thread() is not threading.main_thread():
            self.after(0, lambda m=message, lvl=level: self.log(m, lvl))
            return
        ts = time.strftime("%H:%M:%S")
        prefix = {
            "INFO": "ℹ️ ",
            "SUCCESS": "✓ ",
            "WARN": "⚠️ ",
            "DANGER": "🚨 ",
            "PROCESS": "▶ "
        }.get(level, "")
        line = f"[{ts}] {prefix}{message}\n"
        self.txt_log.insert(tk.END, line)
        self.txt_log.see(tk.END)

    def set_progress(self, value):
        if threading.current_thread() is not threading.main_thread():
            self.after(0, lambda v=value: self.progress_bar.set(v))
        else:
            self.progress_bar.set(value)

    def clear_log(self):
        self.txt_log.delete("1.0", tk.END)

    def check_for_updates(self):
        if not sms_licensing.PACKAGED:
            messagebox.showinfo("檢查更新", "原始碼開發模式不會安裝更新。")
            return
        if not self.ensure_activation():
            return

        def worker():
            try:
                manifest = sms_updater.check_update()
                if manifest is None:
                    self.after(0, lambda: messagebox.showinfo("檢查更新", "目前已是最新版本。"))
                else:
                    self.after(0, lambda m=manifest: self._offer_update(m))
            except Exception as exc:
                self.after(0, lambda error=str(exc): messagebox.showerror("檢查更新失敗", error))

        threading.Thread(target=worker, daemon=True).start()

    def _offer_update(self, manifest):
        version = manifest["version"]
        if not messagebox.askyesno("有新版本", f"發現版本 {version}。現在下載並安裝嗎？"):
            return

        def worker():
            try:
                path = sms_updater.download_update(manifest)
                self.after(0, lambda p=path: self._install_update(p))
            except Exception as exc:
                self.after(0, lambda error=str(exc): messagebox.showerror("下載更新失敗", error))

        threading.Thread(target=worker, daemon=True).start()

    def _install_update(self, path):
        if self.is_running:
            messagebox.showwarning("正在發送", "請先等發送任務完成，再安裝更新。")
            return
        if messagebox.askyesno("安裝更新", "更新檔已驗證。現在關閉程式並安裝嗎？"):
            sms_updater.launch_installer(path)
            self.destroy()

    def save_template(self):
        tpl = self.txt_template.get("1.0", tk.END).strip()
        if not tpl:
            messagebox.showwarning("範本不能為空", "請輸入簡訊範本內容！")
            return
        if save_template_config(tpl):
            self.log("簡訊範本已儲存並永久套用！", "SUCCESS")
            self.update_table_previews()
            messagebox.showinfo("儲存成功", "簡訊範本已儲存成功！下次啟動將自動載入。")
        else:
            messagebox.showerror("儲存失敗", "無法儲存範本檔案，請檢查權限。")

    def reset_template(self):
        if messagebox.askyesno("確認恢復", "是否將範本恢復為官方預設文字？"):
            self.txt_template.delete("1.0", tk.END)
            self.txt_template.insert("1.0", DEFAULT_TEMPLATE)
            save_template_config(DEFAULT_TEMPLATE)
            self.log("簡訊範本已恢復為官方預設值。", "INFO")
            self.update_table_previews()

    def update_table_previews(self):
        tpl = self.txt_template.get("1.0", tk.END).strip()
        for item in self.tree.get_children():
            values = list(self.tree.item(item, 'values'))
            if len(values) >= 5:
                patient_name = values[1]
                values[4] = format_sms_message(tpl, patient_name)
                self.tree.item(item, values=values)

    def on_dry_run_toggle(self):
        if not self.var_dry_run.get():
            confirm = messagebox.askyesno(
                "切換正式發送確認",
                "⚠️ 注意！您即將關閉【模擬測試模式】切換為【正式發送】。\n\n"
                "在此模式下，程式將會實際點擊摩叮網頁的發送按鈕並消耗簡訊額度！\n\n"
                "是否確定切換？"
            )
            if not confirm:
                self.var_dry_run.set(True)
                self.log("使用者取消切換，維持【🛡️ 模擬測試模式】。", "INFO")
            else:
                self.log("已切換為【⚠️ 正式發送模式】！發送時將會實際送出簡訊。", "WARN")
        else:
            self.log("已切換為【🛡️ 模擬測試模式】（只填入網頁預覽，絕對不送出）。", "INFO")

    def on_emergency_stop(self):
        if self.is_running:
            self.stop_requested = True
            self.log("收到緊急停止指令！正在立即中斷後續發送動作...", "DANGER")
        else:
            self.log("目前沒有正在執行的發送任務。", "INFO")

    def scan_excel(self):
        if sms_licensing.PACKAGED and not self.ensure_activation():
            return False
        self.checked_patients = []
        self.log("正在連接 Excel 並掃描【進階篩選】工作表中的核取方塊...", "INFO")
        self.lbl_stats_checked.configure(text="掃描中...")
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.update_idletasks()

        try:
            self.checked_patients = get_checked_patients()
            self._on_scan_completed(self.checked_patients)
            return True
        except Exception as e:
            self._on_scan_error(str(e))
            return False

    def _on_scan_completed(self, patients):
        self.checked_patients = patients
        self.refresh_system_status()
        count = len(self.checked_patients)
        total_mobiles = sum(len(p['mobiles']) for p in self.checked_patients)
        total_landlines = sum(len(p['landlines']) for p in self.checked_patients)

        self.lbl_stats_checked.configure(text=f"已勾選: {count} 人")
        self.lbl_stats_mobiles.configure(text=f"有效手機: {total_mobiles} 支")
        self.lbl_stats_landlines.configure(text=f"已過濾市話: {total_landlines} 筆")

        self.log(f"掃描完成！共發現 {count} 筆勾選病患，預計發送 {total_mobiles} 支手機，已過濾 {total_landlines} 筆市話。", "SUCCESS")
        if any(p.get('workbook_readonly') for p in patients):
            self.log("Excel 目前為唯讀，仍可發送簡訊；發送後將略過取消勾選與存檔，核取方塊會保留勾選。再次發送前請核對紀錄。", "WARN")

        tpl = self.txt_template.get("1.0", tk.END).strip()

        for p in self.checked_patients:
            mobiles_str = ", ".join(p['mobiles']) if p['mobiles'] else "⚠️ (無有效手機)"
            landlines_str = ", ".join(p['landlines']) if p['landlines'] else "-"
            preview_msg = format_sms_message(tpl, p['name'])

            self.tree.insert('', tk.END, values=(
                f"第 {p['row']} 列",
                p['name'],
                mobiles_str,
                landlines_str,
                preview_msg
            ))

    def _on_scan_error(self, err_msg):
        self.checked_patients = []
        self.lbl_stats_checked.configure(text="已勾選: 0 人")
        messagebox.showerror("掃描失敗", err_msg)
        self.log(f"掃描失敗：{err_msg}", "WARN")
        self.refresh_system_status()

    def start_process(self):
        if sms_licensing.PACKAGED and not self.ensure_activation():
            return
        if self.is_running:
            messagebox.showwarning("執行中", "已有發送任務正在執行中！")
            return

        # Always rescan immediately before a run so a stale preview cannot be sent.
        if not self.scan_excel():
            return
        if not self.checked_patients:
            messagebox.showinfo("無待處理名單", "目前在 Excel【進階篩選】中未發現任何被勾選的方塊！\n請先在 Excel 勾選要發送的對象後再點擊掃描。")
            return

        is_dry = self.var_dry_run.get()
        if not is_dry:
            try:
                excel_status = get_excel_status()
                if not excel_status['connected']:
                    messagebox.showerror("Excel 未連線", "找不到寄單工作簿，已禁止正式發送。")
                    return
            except Exception as exc:
                messagebox.showerror("Excel 狀態錯誤", str(exc))
                return

            duplicate_risks = []
            template_text = self.txt_template.get("1.0", tk.END).strip()
            for patient in self.checked_patients:
                message_text = format_sms_message(template_text, patient['name'])
                recent = find_recent_live_attempt(patient['mobiles'], message_text)
                if recent:
                    duplicate_risks.append(
                        f"{patient['name']}（{recent.get('timestamp', '時間不明')}，狀態 {recent.get('status')}）"
                    )
            if duplicate_risks:
                details = "\n".join(duplicate_risks[:8])
                if not messagebox.askyesno(
                    "偵測到重複發送風險",
                    "以下任務在過去 24 小時已有成功或狀態不明的正式紀錄：\n\n"
                    f"{details}\n\n"
                    "請先核對摩叮發送紀錄。只有確認前次沒有送出時，才可選擇『是』重試。"
                ):
                    return

        mode_text = "【🛡️ 模擬測試模式】" if is_dry else "【⚠️ 正式發送模式】"

        confirm_msg = f"即將開始以 {mode_text} 處理 {len(self.checked_patients)} 筆病患簡訊。\n\n"
        if is_dry:
            confirm_msg += "★ 安全保證：測試模式下只會將號碼與內容自動填入摩叮網頁，絕對不會點擊發送！"
        else:
            confirm_msg += "★ 警告：此模式將會實際點擊網頁發送按鈕並消耗簡訊額度！"
            if excel_status['readonly']:
                confirm_msg += "\n\nExcel 目前為唯讀：發送後將略過取消勾選與存檔，核取方塊仍會保留勾選。再次發送前請核對摩叮紀錄，避免重複發送。"

        if not messagebox.askyesno("確認開始", confirm_msg):
            return

        self.is_running = True
        self.stop_requested = False
        self.btn_start.configure(state="disabled")
        self.progress_bar.set(0)

        # Run process in background thread
        template_text = self.txt_template.get("1.0", tk.END).strip()
        patients = [dict(patient) for patient in self.checked_patients]
        worker = threading.Thread(
            target=self._process_worker,
            args=(template_text, is_dry, patients),
            daemon=True
        )
        worker.start()

    def _process_worker(self, template_text, is_dry, patients):
        import pythoncom
        pythoncom.CoInitialize()
        try:
            self._process_worker_inner(template_text, is_dry, patients)
        finally:
            pythoncom.CoUninitialize()

    def _process_worker_inner(self, tpl, is_dry, patients):
        total = len(patients)
        success_count = 0
        readonly_skipped = 0

        self.log(f"=== 開始執行任務 (共 {total} 筆, 模式: {'模擬測試' if is_dry else '正式發送'}) ===", "PROCESS")

        for idx, p in enumerate(patients, 1):
            if self.stop_requested:
                self.log(f"🚨 緊急停止！已在第 {idx-1} 筆中斷處理，剩餘名單已取消。", "DANGER")
                break

            name = p['name']
            row = p['row']
            mobiles = p['mobiles']

            if not mobiles:
                self.log(f"[第 {idx}/{total} 筆] 第 {row} 列【{name}】只有市話無行動電話，自動略過。", "WARN")
                self.set_progress(idx / total)
                continue

            sms_text = format_sms_message(tpl, name)
            self.log(f"[第 {idx}/{total} 筆] 處理第 {row} 列【{name}】，目標門號: {', '.join(mobiles)}", "PROCESS")

            # Call Web Automation module
            ok, reason = fill_moding_and_send(
                mobiles=mobiles,
                message_text=sms_text,
                dry_run=is_dry,
                stop_check_func=lambda: self.stop_requested,
                log_func=lambda msg: self.log(msg, "INFO")
            )

            if self.stop_requested:
                break

            if ok:
                success_count += 1
                if is_dry:
                    self.log(f"  🛡️ 模擬模式：Excel 第 {row} 列保持勾選。", "SUCCESS")
                else:
                    uncheck_ok = uncheck_patient(p)
                    if uncheck_ok is None:
                        readonly_skipped += 1
                        self.log(f"  簡訊已送出；Excel 為唯讀，第 {row} 列已略過取消勾選與存檔，保留勾選。再次發送前請核對紀錄。", "WARN")
                    elif uncheck_ok:
                        self.log(f"  ✓ Excel 第 {row} 列核取方塊已取消勾選並存檔。", "SUCCESS")
                    else:
                        self.log(f"  ⚠️ 簡訊已送出，但 Excel 第 {row} 列無法取消勾選；請立即手動確認，勿重複發送。", "WARN")
            else:
                self.log(f"  ❌ 處理失敗：{reason}", "WARN")

            self.set_progress(idx / total)
            time.sleep(0.5)

        if self.stop_requested:
            self.log("🛑 任務已被使用者緊急中止。", "DANGER")
        else:
            self.log(f"🎉 任務執行完成！共成功處理 {success_count} / {total} 筆病患。", "SUCCESS")

        stopped = bool(self.stop_requested)
        self.after(0, lambda: self._finish_process(stopped, success_count, total, readonly_skipped))

    def _finish_process(self, stopped, success_count, total, readonly_skipped=0):
        self.is_running = False
        self.btn_start.configure(state="normal")
        readonly_notice = (
            f"\n\n其中 {readonly_skipped} 筆簡訊已送出，因 Excel 為唯讀，已略過取消勾選與存檔，核取方塊仍保留勾選。再次發送前請核對摩叮紀錄，避免重複發送。"
            if readonly_skipped else ""
        )
        if stopped:
            messagebox.showwarning("已終止", "發送任務已緊急停止！" + readonly_notice)
        else:
            messagebox.showinfo("完成", f"處理完成！\n共成功處理 {success_count} / {total} 筆。" + readonly_notice)
        self.after(500, self.scan_excel)

if __name__ == '__main__':
    try:
        app = ModernSMSApp()
        app.mainloop()
    except Exception as e:
        import traceback
        err_msg = traceback.format_exc()
        try:
            sms_licensing.STATE_DIR.mkdir(parents=True, exist_ok=True)
            with open(str(sms_licensing.STATE_DIR / "error.log"), "w", encoding="utf-8") as f:
                f.write(err_msg)
            import tkinter.messagebox as mb
            mb.showerror("啟動錯誤", f"程式啟動時發生錯誤：\n{e}\n\n詳細紀錄已存至應用程式資料夾中的 error.log")
        except Exception:
            pass

