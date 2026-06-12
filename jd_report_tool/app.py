from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog

from src.environment import format_environment_report, module_installed
from src.utils import PROJECT_ROOT, ensure_dirs, load_json, normalize_date, open_folder, yesterday_str

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
        self.grid_rowconfigure(5, weight=1)
        base = ctk.CTkFrame(self)
        base.grid(row=0, column=0, padx=12, pady=8, sticky="ew")
        base.grid_columnconfigure((1, 3, 5), weight=1)
        ctk.CTkLabel(base, text="店铺").grid(row=0, column=0, padx=8, pady=8)
        self.store_var = ctk.StringVar(value=self.stores[0]["store_name"] if self.stores else "")
        ctk.CTkOptionMenu(
            base,
            values=[s["store_name"] for s in self.stores],
            variable=self.store_var,
            command=self.on_store_changed,
        ).grid(row=0, column=1, padx=8, pady=8, sticky="ew")
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

        order_frame = ctk.CTkFrame(self)
        order_frame.grid(row=2, column=0, padx=12, pady=8, sticky="ew")
        order_frame.grid_columnconfigure(4, weight=1)
        ctk.CTkLabel(order_frame, text="订单明细").grid(row=0, column=0, padx=8, pady=8)
        self.order_success_var = ctk.BooleanVar(value=True)
        self.order_cancel_var = ctk.BooleanVar(value=True)
        self.order_refund_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(order_frame, text="成交订单", variable=self.order_success_var).grid(row=0, column=1, padx=8, pady=8)
        ctk.CTkCheckBox(order_frame, text="取消订单", variable=self.order_cancel_var).grid(row=0, column=2, padx=8, pady=8)
        ctk.CTkCheckBox(order_frame, text="售后退款订单", variable=self.order_refund_var).grid(row=0, column=3, padx=8, pady=8)
        ctk.CTkButton(order_frame, text="下载所选订单明细", command=self.download_order_details).grid(row=0, column=4, padx=8, pady=8, sticky="ew")

        files = ctk.CTkFrame(self)
        files.grid(row=3, column=0, padx=12, pady=8, sticky="ew")
        buttons = [
            ("选择商品明细-SPU文件", "spu"), ("选择商品明细-SKU文件", "sku"),
            ("选择商品排名定位文件", "rank"), ("选择商品基础信息表", "base"), ("选择参考汇总表", "ref"),
        ]
        for i, (txt, key) in enumerate(buttons):
            ctk.CTkButton(files, text=txt, command=lambda k=key: self.pick_file(k)).grid(row=0, column=i, padx=6, pady=8, sticky="ew")
            files.grid_columnconfigure(i, weight=1)

        dbf = ctk.CTkFrame(self)
        dbf.grid(row=4, column=0, padx=12, pady=8, sticky="ew")
        db_buttons = [
            ("初始化数据库", self.init_db), ("导入商品基础信息", self.import_base), ("查看商品基础信息", self.show_products),
            ("查看SPU/SKU绑定", self.show_mapping), ("查SKU→SPU/货号", self.query_sku), ("查SPU下SKU", self.query_spu),
            ("导入阶段1报表", self.import_stage1_report), ("数据库管理/导入状态", self.show_database_status),
            ("当前汇总写入数据库", self.sync_current), ("导出商品基础信息", self.export_base_from_db), ("打开数据库文件夹", lambda: open_folder(PROJECT_ROOT / "data")),
        ]
        for i, (txt, cmd) in enumerate(db_buttons):
            ctk.CTkButton(dbf, text=txt, command=cmd).grid(row=i // 5, column=i % 5, padx=6, pady=6, sticky="ew")
            dbf.grid_columnconfigure(i % 5, weight=1)

        main = ctk.CTkFrame(self)
        main.grid(row=5, column=0, padx=12, pady=8, sticky="nsew")
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

    def _normalized_dates(self) -> tuple[str, str]:
        start = normalize_date(self.start_var.get(), self.start_var.get())
        end = normalize_date(self.end_var.get(), self.end_var.get())
        self.start_var.set(start)
        self.end_var.set(end)
        return start, end

    def _date_range(self, date_start: str, date_end: str) -> list[str]:
        start = datetime.strptime(date_start, "%Y-%m-%d").date()
        end = datetime.strptime(date_end, "%Y-%m-%d").date()
        if end < start:
            start, end = end, start
        days = []
        current = start
        while current <= end:
            days.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)
        return days

    def _latest_raw_file(self, report_kind: str, day: str) -> Path | None:
        store_name = self.current_store["store_name"]
        if report_kind in {"SPU", "SKU"}:
            folder = PROJECT_ROOT / "data" / "raw" / f"商品明细_{report_kind}" / day
            patterns = [
                f"商品明细_{report_kind}_{store_name}_{day}_{day}*.xlsx",
                f"*{store_name}*{day}*.xlsx",
            ]
        else:
            folder = PROJECT_ROOT / "data" / "raw" / "商品排名定位" / day
            patterns = [
                f"商品排名定位_{store_name}_{day}*.xlsx",
                f"*{store_name}*{day}*.xlsx",
            ]
        candidates: list[Path] = []
        for pattern in patterns:
            candidates.extend(folder.glob(pattern))
        candidates = [path for path in candidates if path.is_file()]
        return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None

    def _summary_input_files(self, date_start: str, date_end: str) -> tuple[list[Path], list[Path], list[Path], list[str]]:
        days = self._date_range(date_start, date_end)
        missing: list[str] = []

        def collect(kind: str, required: bool = True) -> list[Path]:
            files: list[Path] = []
            for day in days:
                file = self._latest_raw_file(kind, day)
                if file:
                    files.append(file)
                elif required:
                    missing.append(f"{day} {kind}")
            return files

        spu_files = collect("SPU")
        sku_files = collect("SKU")
        rank_files = collect("rank", required=False)

        if not spu_files and self.files.get("spu"):
            spu_files = [Path(self.files["spu"])]
        if not sku_files and self.files.get("sku"):
            sku_files = [Path(self.files["sku"])]
        if not rank_files and self.files.get("rank"):
            rank_files = [Path(self.files["rank"])]
        return spu_files, sku_files, rank_files, missing

    async def _download_product_range(self, downloader, tab: str, date_start: str, date_end: str) -> list[Path]:
        path = await downloader.download(self.current_store, tab, date_start, date_end)
        if path:
            return [path]
        days = self._date_range(date_start, date_end)
        if len(days) <= 1:
            return []
        self._set_status(f"商品明细-{tab} 多天下载失败，自动改为逐天下载")
        paths: list[Path] = []
        for day in days:
            day_path = await downloader.download(self.current_store, tab, day, day)
            if day_path:
                paths.append(day_path)
        return paths

    async def _download_rank_range(self, downloader, date_start: str, date_end: str) -> list[Path]:
        paths: list[Path] = []
        for day in self._date_range(date_start, date_end):
            path = await downloader.download(self.current_store, day)
            if path:
                paths.append(path)
        return paths

    def _run_thread(self, func):
        threading.Thread(target=func, daemon=True).start()

    async def _close_browser(self) -> None:
        if self.browser is not None:
            try:
                await self.browser.close()
            finally:
                self.browser = None

    def _new_browser_manager(self):
        from src.browser import BrowserManager, store_user_data_dir

        store = self.current_store
        return BrowserManager(user_data_dir=store_user_data_dir(store), use_default_browser=False)

    def on_store_changed(self, _store_name: str | None = None) -> None:
        def task():
            try:
                asyncio.run(self._close_browser())
                self._set_status("已切换店铺；下次打开浏览器会使用该店铺的独立账户")
            except Exception as exc:
                self._set_status(f"切换店铺时关闭浏览器失败: {exc}")

        self._run_thread(task)

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
            from src.downloader_product_detail import PRODUCT_DETAIL_URL

            async def open_login_page() -> None:
                await self._close_browser()
                self.browser = self._new_browser_manager()
                page = await self.browser.new_page()
                await page.goto(PRODUCT_DETAIL_URL, wait_until="domcontentloaded")

            try:
                asyncio.run(open_login_page())
                self._set_status("已打开当前店铺的独立工具浏览器，请在其中手动登录京东商智")
            except Exception as exc:
                self._set_status(f"独立工具浏览器打开失败: {exc}")
                messagebox.showerror("独立工具浏览器打开失败", str(exc))
        self._run_thread(task)

    def run_download(self, tab: str) -> None:
        def task():
            try:
                if not self._require_modules(["playwright"]):
                    return
                from src.downloader_product_detail import ProductDetailDownloader

                date_start, date_end = self._normalized_dates()
                async def run_once():
                    await self._close_browser()
                    self.browser = self._new_browser_manager()
                    try:
                        downloader = ProductDetailDownloader(self.browser, self.db)
                        return await self._download_product_range(downloader, tab, date_start, date_end)
                    finally:
                        await self._close_browser()

                paths = asyncio.run(run_once())
                if paths:
                    self.files["spu" if tab == "SPU" else "sku"] = str(paths[-1])
                    self._set_status(f"商品明细-{tab} 下载成功: {len(paths)}份; 最新文件: {paths[-1]}")
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
                from src.downloader_rank_location import RankLocationDownloader

                date_start, date_end = self._normalized_dates()
                async def run_once():
                    await self._close_browser()
                    self.browser = self._new_browser_manager()
                    try:
                        downloader = RankLocationDownloader(self.browser, self.db)
                        return await self._download_rank_range(downloader, date_start, date_end)
                    finally:
                        await self._close_browser()

                paths = asyncio.run(run_once())
                if paths:
                    self.files["rank"] = str(paths[-1])
                    self._set_status(f"商品排名定位下载成功: {len(paths)}份; 最新文件: {paths[-1]}")
                else:
                    self._set_status("商品排名定位下载失败，已记录日志")
            except Exception as exc:
                self._set_status(f"异常: {exc}")
        self._run_thread(task)

    def selected_order_report_types(self) -> list[str]:
        selected: list[str] = []
        if self.order_success_var.get():
            selected.append("trade_order_success")
        if self.order_cancel_var.get():
            selected.append("trade_order_cancel")
        if self.order_refund_var.get():
            selected.append("trade_order_refund")
        return selected

    def download_order_details(self) -> None:
        def task():
            try:
                if not self._require_modules(["playwright"]):
                    return
                selected = self.selected_order_report_types()
                if not selected:
                    self._set_status("请至少勾选一个订单明细报表")
                    messagebox.showwarning("未选择报表", "请至少勾选一个订单明细报表")
                    return
                from src.downloader_order_details import ORDER_REPORTS, OrderDetailsDownloader

                date_start, date_end = self._normalized_dates()

                async def download_sequence():
                    await self._close_browser()
                    self.browser = self._new_browser_manager()
                    downloader = OrderDetailsDownloader(self.browser, self.db)
                    results = []
                    try:
                        for report_type in selected:
                            path = await downloader.download(self.current_store, report_type, date_start, date_end)
                            results.append((report_type, path))
                        return results
                    finally:
                        await self._close_browser()

                for report_type, path in asyncio.run(download_sequence()):
                    name = ORDER_REPORTS[report_type].report_name
                    if path:
                        self._set_status(f"订单明细-{name} 下载成功: {path}")
                    else:
                        self._set_status(f"订单明细-{name} 未下载成功，已记录 download_logs")
            except Exception as exc:
                self._set_status(f"订单明细下载异常: {exc}")
                messagebox.showerror("订单明细下载失败", str(exc))
        self._run_thread(task)

    def download_all(self) -> None:
        def task():
            try:
                if not self._require_modules(["playwright"]):
                    return
                from src.downloader_product_detail import ProductDetailDownloader
                from src.downloader_rank_location import RankLocationDownloader

                date_start, date_end = self._normalized_dates()

                async def download_sequence():
                    await self._close_browser()
                    self.browser = self._new_browser_manager()
                    try:
                        product_downloader = ProductDetailDownloader(self.browser, self.db)
                        rank_downloader = RankLocationDownloader(self.browser, self.db)
                        spu = await self._download_product_range(product_downloader, "SPU", date_start, date_end)
                        sku = await self._download_product_range(product_downloader, "SKU", date_start, date_end)
                        rank = await self._download_rank_range(rank_downloader, date_start, date_end)
                        return spu, sku, rank
                    finally:
                        await self._close_browser()

                spu_paths, sku_paths, rank_paths = asyncio.run(download_sequence())
                if spu_paths:
                    self.files["spu"] = str(spu_paths[-1])
                    self._set_status(f"商品明细-SPU 下载成功: {len(spu_paths)}份; 最新文件: {spu_paths[-1]}")
                else:
                    self._set_status("商品明细-SPU 下载失败，已记录日志")

                if sku_paths:
                    self.files["sku"] = str(sku_paths[-1])
                    self._set_status(f"商品明细-SKU 下载成功: {len(sku_paths)}份; 最新文件: {sku_paths[-1]}")
                else:
                    self._set_status("商品明细-SKU 下载失败，已记录日志")

                if rank_paths:
                    self.files["rank"] = str(rank_paths[-1])
                    self._set_status(f"商品排名定位下载成功: {len(rank_paths)}份; 最新文件: {rank_paths[-1]}")
                else:
                    self._set_status("商品排名定位下载失败，已记录日志")
            except Exception as exc:
                self._set_status(f"异常: {exc}")
                messagebox.showerror("下载失败", str(exc))
        self._run_thread(task)

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

    def import_stage1_report(self) -> None:
        report_types = {
            "SKU商品明细日报": "product_detail_sku_daily",
            "SPU商品明细日报": "product_detail_spu_daily",
            "成交订单明细": "order_paid_items",
            "取消订单明细": "order_cancel_items",
            "售后退款订单": "order_refund_items",
            "自主售后服务单": "aftersale_services",
            "搜索分析-排名定位": "search_rank_sku_daily",
            "商品基础信息": "product_master_sku",
        }
        if not self._require_modules(["pandas", "openpyxl"]):
            return
        path = filedialog.askopenfilename(filetypes=[("Excel", "*.xlsx *.xls")])
        if not path:
            return
        win = ctk.CTkToplevel(self)
        win.title("选择报表类型")
        win.geometry("420x180")
        selected = ctk.StringVar(value="SKU商品明细日报")
        ctk.CTkLabel(win, text="请选择这个 Excel 对应的报表类型").pack(padx=16, pady=(18, 8))
        ctk.CTkOptionMenu(win, values=list(report_types.keys()), variable=selected).pack(padx=16, pady=8, fill="x")

        def run_import():
            win.destroy()
            try:
                from src.db_importer import JDDataImporter

                result = JDDataImporter(self.db).import_file(
                    self.current_store["store_id"], path, report_types[selected.get()], self.start_var.get(), self.end_var.get()
                )
                self._set_status(f"阶段1报表导入完成: {result}")
            except Exception as exc:
                messagebox.showerror("导入失败", str(exc))
                self._set_status(f"阶段1报表导入失败: {exc}")

        ctk.CTkButton(win, text="开始导入", command=run_import).pack(padx=16, pady=14)

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
        self.show_table_query("商品基础信息", "SELECT * FROM dim_sku WHERE store_id=? ORDER BY sku LIMIT 1000", (self.current_store["store_id"],))

    def show_mapping(self):
        self.show_table_query("SPU/SKU绑定关系", "SELECT * FROM product_mapping WHERE store_id=? ORDER BY spu, sku LIMIT 1000", (self.current_store["store_id"],))

    def show_database_status(self):
        if self.db is None:
            messagebox.showerror("数据库不可用", str(_database_import_error))
            return
        summary = self.db.get_import_status_summary(self.current_store["store_id"])
        logs = self.db.get_recent_import_logs(50)
        win = ctk.CTkToplevel(self)
        win.title("数据库管理/导入状态")
        win.geometry("1100x720")
        box = ctk.CTkTextbox(win)
        box.pack(fill="both", expand=True, padx=10, pady=10)
        labels = [
            ("当前数据库路径", "db_path"),
            ("店铺数量", "stores"),
            ("SPU数量", "spu"),
            ("SKU数量", "sku"),
            ("商品映射数量", "mapping"),
            ("订单数量", "orders"),
            ("取消订单数量", "cancel_orders"),
            ("售后退款数量", "refunds"),
            ("自主售后服务单数量", "aftersales"),
            ("搜索排名数据日期数量", "search_rank_dates"),
            ("最近一次导入时间", "latest_import_time"),
            ("最近一次导入报表类型", "latest_import_type"),
            ("未匹配SKU数量", "unmatched_sku"),
            ("导入失败文件数量", "failed_files"),
        ]
        for label, key in labels:
            box.insert("end", f"{label}: {summary.get(key, '')}\n")
        box.insert("end", "\n最近50条导入日志:\n")
        for row in logs:
            box.insert("end", f"{row}\n")

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
            date_start, date_end = self._normalized_dates()
            spu_files, sku_files, rank_files, missing = self._summary_input_files(date_start, date_end)
            if not spu_files or not sku_files:
                messagebox.showwarning("缺少文件", "请先下载或选择日期范围内的 SPU 和 SKU 报表文件")
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
                spu_files, sku_files, rank_files, self.current_store["store_id"],
                self.db, self.files.get("base"), date_start,
            )
            self.last_output = export_summary(
                self.last_spu_df, self.last_sku_df, self.current_store["store_name"],
                date_start, date_end,
            )
            result = sync_summary_to_db(self.current_store["store_id"], self.last_spu_df, self.last_sku_df, self.db)
            warn = f"; 缺少文件: {', '.join(missing)}" if missing else ""
            self._set_status(
                f"汇总成功: {self.last_output}; SPU文件{len(spu_files)}份, SKU文件{len(sku_files)}份, 排名文件{len(rank_files)}份; "
                f"数据库写入: {result}{warn}"
            )
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
        rows = self.db.query("SELECT * FROM dim_sku WHERE store_id=? ORDER BY spu, sku", (self.current_store["store_id"],))
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
