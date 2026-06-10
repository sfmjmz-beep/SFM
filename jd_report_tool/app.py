from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog

from src.environment import format_environment_report, module_installed
from src.utils import PROJECT_ROOT, ensure_dirs, load_json, open_folder, yesterday_str

if not module_installed("customtkinter"):
    msg = "缺少运行依赖：customtkinter\n请先运行 install.bat，或执行 pip install -r requirements.txt 后再启动。"
    print(msg)
    try:
        import tkinter as _tk
        from tkinter import messagebox as _messagebox

        _root = _tk.Tk()
        _root.withdraw()
        _messagebox.showerror("京东商智报表工具 - 依赖缺失", msg)
        _root.destroy()
    except Exception:
        pass
    raise SystemExit(2)

import customtkinter as ctk

try:
    from src.database import Database
except Exception as exc:
    Database = None  # type: ignore[assignment]
    _database_import_error = exc
else:
    _database_import_error = None

from src.logger import logger


class JDReportApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        ensure_dirs()
        self.title("京东商智报表下载与SPU/SKU汇总工具")
        self.geometry("1180x820")
        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")
        if Database is None:
            self.db = None
        else:
            self.db = Database()
        self.browser: BrowserManager | None = None
        self.files: dict[str, str] = {}
        self.last_spu_df = None
        self.last_sku_df = None
        self.last_output: Path | None = None
        self.stores = [s for s in load_json(PROJECT_ROOT / "config" / "stores.json").get("stores", []) if s.get("enabled", True)]
        self._build_ui()
        if self.db is not None:
            self.db.initialize()
            self._set_status("程序已启动，数据库已初始化")
        else:
            self._set_status(f"数据库模块加载失败: {_database_import_error}")

    @property
    def current_store(self) -> dict:
        name = self.store_var.get()
        return next((s for s in self.stores if s["store_name"] == name), self.stores[0])

    def _require_modules(self, modules: list[str]) -> bool:
        missing = [m for m in modules if not module_installed(m)]
        if missing:
            msg = "缺少依赖：" + ", ".join(missing) + "。请先运行 install.bat。"
            self._set_status(msg)
            messagebox.showerror("依赖缺失", msg)
            return False
        return True


    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)
        base = ctk.CTkFrame(self)
        base.grid(row=0, column=0, padx=12, pady=8, sticky="ew")
        base.grid_columnconfigure((1, 3, 5), weight=1)
        ctk.CTkLabel(base, text="店铺").grid(row=0, column=0, padx=8, pady=8)
        self.store_var = ctk.StringVar(value=self.stores[0]["store_name"] if self.stores else "")
        ctk.CTkOptionMenu(base, values=[s["store_name"] for s in self.stores], variable=self.store_var).grid(row=0, column=1, padx=8, pady=8, sticky="ew")
        ctk.CTkLabel(base, text="开始日期").grid(row=0, column=2, padx=8, pady=8)
        self.start_var = ctk.StringVar(value=yesterday_str())
        ctk.CTkEntry(base, textvariable=self.start_var).grid(row=0, column=3, padx=8, pady=8, sticky="ew")
        ctk.CTkLabel(base, text="结束日期").grid(row=0, column=4, padx=8, pady=8)
        self.end_var = ctk.StringVar(value=yesterday_str())
        ctk.CTkEntry(base, textvariable=self.end_var).grid(row=0, column=5, padx=8, pady=8, sticky="ew")

        download = ctk.CTkFrame(self)
        download.grid(row=1, column=0, padx=12, pady=8, sticky="ew")
        for i, (txt, cmd) in enumerate([
            ("检查环境", self.check_environment), ("登录/打开浏览器", self.open_browser),
            ("下载商品明细-SPU", lambda: self.run_download("SPU")),
            ("下载商品明细-SKU", lambda: self.run_download("SKU")), ("下载商品排名定位", self.run_rank_download),
            ("一键下载全部报表", self.download_all),
        ]):
            ctk.CTkButton(download, text=txt, command=cmd).grid(row=0, column=i, padx=6, pady=8, sticky="ew")
            download.grid_columnconfigure(i, weight=1)

        files = ctk.CTkFrame(self)
        files.grid(row=2, column=0, padx=12, pady=8, sticky="ew")
        buttons = [
            ("选择商品明细-SPU文件", "spu"), ("选择商品明细-SKU文件", "sku"),
            ("选择商品排名定位文件", "rank"), ("选择商品基础信息表", "base"), ("选择参考汇总表", "ref"),
        ]
        for i, (txt, key) in enumerate(buttons):
            ctk.CTkButton(files, text=txt, command=lambda k=key: self.pick_file(k)).grid(row=0, column=i, padx=6, pady=8, sticky="ew")
            files.grid_columnconfigure(i, weight=1)

        dbf = ctk.CTkFrame(self)
        dbf.grid(row=3, column=0, padx=12, pady=8, sticky="ew")
        db_buttons = [
            ("初始化数据库", self.init_db), ("导入商品基础信息", self.import_base), ("查看商品基础信息", self.show_products),
            ("查看SPU/SKU绑定", self.show_mapping), ("查SKU→SPU/货号", self.query_sku), ("查SPU下SKU", self.query_spu),
            ("当前汇总写入数据库", self.sync_current), ("导出商品基础信息", self.export_base_from_db), ("打开数据库文件夹", lambda: open_folder(PROJECT_ROOT / "data" / "database")),
        ]
        for i, (txt, cmd) in enumerate(db_buttons):
            ctk.CTkButton(dbf, text=txt, command=cmd).grid(row=i // 5, column=i % 5, padx=6, pady=6, sticky="ew")
            dbf.grid_columnconfigure(i % 5, weight=1)

        main = ctk.CTkFrame(self)
        main.grid(row=4, column=0, padx=12, pady=8, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)
        top = ctk.CTkFrame(main)
        top.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(top, text="一键生成 SPU/SKU 汇总表", command=self.generate_summary).pack(side="left", padx=8, pady=8)
        ctk.CTkButton(top, text="打开结果文件夹", command=lambda: open_folder(PROJECT_ROOT / "data" / "output")).pack(side="left", padx=8, pady=8)
        ctk.CTkButton(top, text="查看运行日志", command=lambda: self.show_text_file(PROJECT_ROOT / "logs" / "run.log")).pack(side="left", padx=8, pady=8)
        self.status_box = ctk.CTkTextbox(main)
        self.status_box.grid(row=1, column=0, sticky="nsew", padx=8, pady=8)

    def _set_status(self, text: str) -> None:
        msg = f"当前店铺: {self.store_var.get()} | 日期: {self.start_var.get()} ~ {self.end_var.get()} | {text}\n"
        logger.info(msg.strip())
        self.status_box.insert("end", msg)
        self.status_box.see("end")

    def _run_thread(self, func):
        threading.Thread(target=func, daemon=True).start()

    def check_environment(self) -> None:
        from src.environment import check_dependencies, format_environment_report

        report = format_environment_report(check_dependencies())
        self._set_status("环境检查完成")
        win = ctk.CTkToplevel(self)
        win.title("环境检查")
        win.geometry("900x520")
        box = ctk.CTkTextbox(win)
        box.pack(fill="both", expand=True, padx=10, pady=10)
        box.insert("end", report)
        if "❌" in report:
            messagebox.showwarning("环境检查", "发现缺失项，请按窗口中的修复建议处理。")
        else:
            messagebox.showinfo("环境检查", "环境检查通过。")

    def open_browser(self) -> None:
        def task():
            if not self._require_modules(["playwright"]):
                return
            from src.browser import BrowserManager

            self.browser = BrowserManager()
            asyncio.run(self.browser.start())
            self._set_status("浏览器已打开，请手动登录京东商智；遇到验证码请手动处理")
        self._run_thread(task)

    def run_download(self, tab: str) -> None:
        def task():
            try:
                if not self._require_modules(["playwright"]):
                    return
                from src.browser import BrowserManager
                from src.downloader_product_detail import ProductDetailDownloader

                if not self.browser:
                    self.browser = BrowserManager()
                downloader = ProductDetailDownloader(self.browser, self.db)
                path = asyncio.run(downloader.download(self.current_store, tab, self.start_var.get(), self.end_var.get()))
                if path:
                    self.files["spu" if tab == "SPU" else "sku"] = str(path)
                    self._set_status(f"商品明细-{tab} 下载成功: {path}")
                else:
                    self._set_status(f"商品明细-{tab} 下载失败，已记录日志")
            except Exception as exc:
                self._set_status(f"异常: {exc}")
        self._run_thread(task)

    def run_rank_download(self) -> None:
        def task():
            try:
                if not self._require_modules(["playwright"]):
                    return
                from src.browser import BrowserManager
                from src.downloader_rank_location import RankLocationDownloader

                if not self.browser:
                    self.browser = BrowserManager()
                path = asyncio.run(RankLocationDownloader(self.browser, self.db).download(self.current_store, self.end_var.get()))
                if path:
                    self.files["rank"] = str(path)
                    self._set_status(f"商品排名定位下载成功: {path}")
                else:
                    self._set_status("商品排名定位下载失败，已记录日志")
            except Exception as exc:
                self._set_status(f"异常: {exc}")
        self._run_thread(task)

    def download_all(self) -> None:
        self.run_download("SPU")
        self.run_download("SKU")
        self.run_rank_download()

    def pick_file(self, key: str) -> None:
        path = filedialog.askopenfilename(filetypes=[("Excel", "*.xlsx *.xls"), ("All", "*.*")])
        if path:
            self.files[key] = path
            self._set_status(f"已选择 {key}: {path}")

    def init_db(self) -> None:
        if self.db is None:
            self._set_status(f"数据库模块加载失败: {_database_import_error}")
            return
        self.db.initialize()
        self._set_status("数据库初始化完成")

    def import_base(self) -> None:
        path = self.files.get("base") or filedialog.askopenfilename(filetypes=[("Excel", "*.xlsx *.xls")])
        if not path:
            return
        try:
            if not self._require_modules(["pandas", "openpyxl"]):
                return
            from src.db_importer import BaseInfoImporter

            result = BaseInfoImporter(self.db).import_base_info(self.current_store["store_id"], path)
            self._set_status(f"导入商品基础信息成功: {result}")
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc))
            self._set_status(f"导入商品基础信息失败: {exc}")

    def show_table_query(self, title: str, sql: str, params=()) -> None:
        if self.db is None:
            rows = [{"error": f"数据库模块加载失败: {_database_import_error}"}]
        else:
            rows = self.db.query(sql, params)
        win = ctk.CTkToplevel(self)
        win.title(title)
        win.geometry("900x500")
        box = ctk.CTkTextbox(win)
        box.pack(fill="both", expand=True)
        for row in rows[:1000]:
            box.insert("end", f"{row}\n")

    def show_products(self):
        self.show_table_query("商品基础信息", "SELECT * FROM products_sku WHERE store_id=? ORDER BY sku LIMIT 1000", (self.current_store["store_id"],))

    def show_mapping(self):
        self.show_table_query("SPU/SKU绑定关系", "SELECT * FROM product_mapping WHERE store_id=? ORDER BY spu, sku LIMIT 1000", (self.current_store["store_id"],))

    def query_sku(self):
        sku = simpledialog.askstring("查询SKU", "请输入 SKU：")
        if sku:
            if self.db is None:
                messagebox.showerror("数据库不可用", str(_database_import_error))
                return
            messagebox.showinfo("查询结果", str(self.db.get_mapping_by_sku(self.current_store["store_id"], sku) or {}))

    def query_spu(self):
        spu = simpledialog.askstring("查询SPU", "请输入 SPU：")
        if spu:
            if self.db is None:
                messagebox.showerror("数据库不可用", str(_database_import_error))
                return
            messagebox.showinfo("查询结果", str(self.db.get_mapping_by_spu(self.current_store["store_id"], spu)))

    def generate_summary(self) -> None:
        try:
            if not self.files.get("spu") or not self.files.get("sku"):
                messagebox.showwarning("缺少文件", "请先选择 SPU 和 SKU 报表文件")
                return
            if not self._require_modules(["pandas", "openpyxl"]):
                return
            from src.db_importer import BaseInfoImporter, sync_summary_to_db
            from src.excel_processor import build_summary
            from src.exporter import export_summary

            if self.files.get("base"):
                try:
                    BaseInfoImporter(self.db).import_base_info(self.current_store["store_id"], self.files["base"])
                except Exception as exc:
                    self._set_status(f"基础信息导入失败但继续汇总: {exc}")
            self.last_spu_df, self.last_sku_df = build_summary(
                self.files["spu"], self.files["sku"], self.files.get("rank"), self.current_store["store_id"],
                self.db, self.files.get("base"), self.start_var.get(),
            )
            self.last_output = export_summary(
                self.last_spu_df, self.last_sku_df, self.current_store["store_name"],
                self.start_var.get(), self.end_var.get()
            )
            result = sync_summary_to_db(self.current_store["store_id"], self.last_spu_df, self.last_sku_df, self.db)
            self._set_status(f"汇总成功: {self.last_output}; 数据库写入: {result}")
        except Exception as exc:
            logger.exception("生成汇总失败: %s", exc)
            messagebox.showerror("生成失败", str(exc))
            self._set_status(f"汇总失败: {exc}")

    def sync_current(self):
        if self.last_spu_df is None or self.last_sku_df is None:
            messagebox.showwarning("无汇总数据", "请先生成汇总表")
            return
        if not self._require_modules(["pandas", "openpyxl"]):
            return
        from src.db_importer import sync_summary_to_db

        result = sync_summary_to_db(self.current_store["store_id"], self.last_spu_df, self.last_sku_df, self.db)
        self._set_status(f"当前汇总写入数据库成功: {result}")

    def export_base_from_db(self):
        if self.db is None:
            messagebox.showerror("数据库不可用", str(_database_import_error))
            return
        rows = self.db.query("SELECT * FROM products_sku WHERE store_id=? ORDER BY spu, sku", (self.current_store["store_id"],))
        if not rows:
            messagebox.showinfo("无数据", "数据库里暂无商品基础信息")
            return
        if not self._require_modules(["pandas", "openpyxl"]):
            return
        import pandas as pd
        out = PROJECT_ROOT / "data" / "output" / f"{self.current_store['store_name']}_商品基础信息_数据库导出.xlsx"
        pd.DataFrame(rows).to_excel(out, index=False)
        self._set_status(f"数据库商品基础信息已导出: {out}")

    def show_text_file(self, path: Path) -> None:
        win = ctk.CTkToplevel(self)
        win.title(str(path))
        win.geometry("1000x650")
        box = ctk.CTkTextbox(win)
        box.pack(fill="both", expand=True)
        if path.exists():
            box.insert("end", path.read_text(encoding="utf-8", errors="ignore")[-50000:])


if __name__ == "__main__":
    app = JDReportApp()
    app.mainloop()
