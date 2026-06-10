# 京东商智自动报表下载 + 本地数据库 + SPU/SKU 数据汇总工具

第一版使用 Python 开发，支持：

- 打开京东商智页面并复用浏览器登录状态，不保存京东账号密码。
- 下载「商品明细-SPU」「商品明细-SKU」「商品排名定位」。
- 手动选择下载失败时的 Excel 文件继续生成汇总。
- 读取商品基础信息，建立 `SKU → SPU → 货号` 映射。
- 生成固定格式 Excel：`SPU数据汇总`、`SKU数据汇总`。
- 同步写入 SQLite 本地数据库。
- 预留 FastAPI 本地接口，便于后续看板、分析软件调用。

## 目录结构

```text
jd_report_tool/
├─ app.py
├─ requirements.txt
├─ README.md
├─ config/
│  ├─ stores.json
│  └─ report_tasks.json
├─ src/
│  ├─ browser.py
│  ├─ downloader_product_detail.py
│  ├─ downloader_rank_location.py
│  ├─ database.py
│  ├─ db_models.py
│  ├─ db_importer.py
│  ├─ excel_processor.py
│  ├─ mapper.py
│  ├─ exporter.py
│  ├─ api_server.py
│  ├─ logger.py
│  └─ utils.py
├─ data/
│  ├─ raw/
│  │  ├─ 商品明细_SPU/
│  │  ├─ 商品明细_SKU/
│  │  └─ 商品排名定位/
│  ├─ base/
│  ├─ output/
│  └─ database/
├─ logs/
└─ samples/
```

## 安装

### 1. 准备 Python

建议 Windows 安装 Python 3.10 或以上版本。

### 2. 创建虚拟环境

```bash
cd jd_report_tool
python -m venv .venv
.venv\Scripts\activate
```

macOS/Linux：

```bash
cd jd_report_tool
python3 -m venv .venv
source .venv/bin/activate
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
playwright install chromium
```

如电脑已安装 Chrome，程序会优先尝试通过 Playwright 的 `channel="chrome"` 启动本机 Chrome；失败时自动改用 Playwright Chromium。

## 配置店铺

编辑 `config/stores.json`：

```json
{
  "stores": [
    {
      "store_id": 1,
      "store_name": "艺颂",
      "shop_id": "14975660",
      "platform": "JD",
      "enabled": true
    }
  ]
}
```

多店铺时追加多个对象，`store_id` 必须唯一。

## 如何登录京东商智

1. 启动工具：
   ```bash
   python app.py
   ```
2. 点击「登录/打开浏览器」。
3. 在浏览器中手动登录京东商智。
4. 如出现验证码、安全验证、滑块，请手动处理；程序不会绕过验证码。
5. 登录状态保存在 `data/chrome_user_data/`，代码中不会保存账号密码。

## 如何下载报表

界面中选择店铺和日期范围后，可点击：

- 「下载商品明细-SPU」
- 「下载商品明细-SKU」
- 「下载商品排名定位」
- 「一键下载全部报表」

下载保存位置：

- `data/raw/商品明细_SPU/{日期}/`
- `data/raw/商品明细_SKU/{日期}/`
- `data/raw/商品排名定位/{日期}/`

商品明细 SPU/SKU 会进入下载中心轮询等待「已生成」，最多等待 5 分钟。商品排名定位使用浏览器直接下载事件。

> 京东商智页面结构可能调整，若自动定位失败，可先手动下载文件，再通过界面手动选择文件继续汇总。

## 如何手动选择文件并生成汇总

1. 点击「选择商品明细-SPU文件」。
2. 点击「选择商品明细-SKU文件」。
3. 点击「选择商品排名定位文件」（可选，但用于补充 SKU 类目排名）。
4. 点击「选择商品基础信息表」。
5. 点击「一键生成 SPU/SKU 汇总表」。

输出文件在：

```text
data/output/{店铺名}_SPU与SKU数据汇总_{开始日期}_{结束日期}.xlsx
```

包含两个工作表：

- `SPU数据汇总`
- `SKU数据汇总`

字段顺序固定，SPU/SKU/货号按文本格式输出，金额保留两位小数，比例按百分比显示。

## 如何导入数据库

点击「导入商品基础信息」会写入：

- `products_spu`
- `products_sku`
- `product_mapping`

生成汇总表后，程序会自动同步写入：

- `daily_spu_data`
- `daily_sku_data`

数据库文件：

```text
data/database/jd_operation.db
```

同一天、同店铺、同 SPU/SKU 的数据会覆盖更新，不重复插入。

## 如何查看数据

界面提供：

- 查看商品基础信息
- 查看 SPU/SKU 绑定关系
- 查询某个 SKU 对应的 SPU 和货号
- 查询某个 SPU 下所有 SKU
- 从数据库导出商品基础信息
- 打开数据库文件夹

也可以用 SQLite 工具打开 `data/database/jd_operation.db`。

## 本地 API（默认不启动）

启动：

```bash
cd jd_report_tool
uvicorn src.api_server:app --host 127.0.0.1 --port 8000
```

预留接口：

- `GET /stores`
- `GET /products/spu?store_id=1`
- `GET /products/sku?store_id=1`
- `GET /mapping/by-sku?store_id=1&sku=xxx`
- `GET /mapping/by-spu?store_id=1&spu=xxx`
- `GET /daily/spu?store_id=1&date_start=2026-06-01&date_end=2026-06-09`
- `GET /daily/sku?store_id=1&date_start=2026-06-01&date_end=2026-06-09`
- `POST /import/base-info`
- `POST /sync/report-data`

## 日志与截图

日志：

```text
logs/run.log
```

失败截图：

```text
logs/screenshots/
```

日志会记录：店铺、任务、日期、页面 URL、步骤、下载时间、原始文件名、保存路径、汇总输出路径、数据库写入结果、字段缺失、映射缺失、异常原因等。

## 测试步骤

```bash
python -m compileall app.py src
```

```bash
python - <<'PY'
from src.database import Database
Database().initialize()
print('ok')
PY
```

如有样例 Excel 文件，把它们放入 `samples/` 或任意目录，然后用界面手动选择：

1. 商品明细 SPU 文件
2. 商品明细 SKU 文件
3. 商品排名定位文件
4. 商品基础信息表
5. 点击生成汇总

## 常见错误处理

### 1. Playwright 找不到浏览器

运行：

```bash
playwright install chromium
```

或安装 Google Chrome。

### 2. 京东页面出现验证码

请手动完成验证码。程序只会暂停/等待，不会绕过验证码。

### 3. 下载中心超过 5 分钟未生成

程序会记录失败并继续下一个任务。你可以手动从商智下载中心下载文件，再在界面选择文件生成汇总。

### 4. SPU/SKU 显示科学计数法

工具读取和输出时会强制将 SPU、SKU、货号按文本处理。若原始 Excel 已经把 ID 破坏成科学计数法且丢失精度，需要重新从京东导出原始文件。

### 5. 找不到 SKU → SPU → 货号映射

先导入最新商品基础信息表。仍找不到时，工具会保留该行，SPU/货号为空，并写入日志。

### 6. 字段名和样例不完全一致

第一版使用模糊字段识别，例如优先找包含「曝光」的字段，没有则用「商品浏览量/浏览量」替代；优先找包含「点击」的字段，没有则用「商品访客数/访客数」替代，并记录日志。

## Windows 一键运行流程（推荐）

### 第一步：安装 Python

1. 打开 Python 官网下载 Python 3.10、3.11 或 3.12。
2. 安装时必须勾选 `Add Python to PATH`。
3. 安装完成后打开命令提示符，执行：

```bat
python --version
```

能看到 Python 版本号即表示安装成功。

### 第二步：运行 install.bat

双击项目目录下的：

```text
install.bat
```

脚本会自动完成：

1. 创建 `.venv` 虚拟环境。
2. 升级 pip。
3. 安装 `requirements.txt` 中的 pandas、openpyxl、playwright、customtkinter、fastapi 等依赖。
4. 执行 `python -m playwright install chromium` 安装 Playwright Chromium 浏览器。

如果安装失败，通常是网络或 Python 版本问题。可在命令行中手动执行：

```bat
cd jd_report_tool
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m playwright install chromium
```

### 第三步：运行 run.bat

双击：

```text
run.bat
```

如果依赖缺失，脚本会明确提示缺少哪些依赖，而不是直接闪退。

### 第四步：打开浏览器登录京东商智

1. 在界面点击「登录/打开浏览器」。
2. 在打开的 Chrome/Chromium 浏览器里手动登录京东商智。
3. 如果出现验证码、滑块或安全验证，请手动处理。
4. 工具不会保存京东账号密码，也不会绕过验证码。

### 第五步：选择文件生成汇总表

首次验证建议先用 `samples/` 目录里的 demo 文件：

1. 选择 `samples/商品明细_SPU_样例.xlsx`。
2. 选择 `samples/商品明细_SKU_样例.xlsx`。
3. 选择 `samples/商品排名定位_样例.xlsx`。
4. 选择 `samples/商品基础信息_样例.xlsx`。
5. 点击「一键生成 SPU/SKU 汇总表」。
6. 到 `data/output/` 查看输出文件。

### 第六步：使用自动下载功能

登录京东商智后，可在界面选择店铺与日期，然后点击：

- 下载商品明细-SPU
- 下载商品明细-SKU
- 下载商品排名定位
- 一键下载全部报表

如果京东页面结构调整导致自动下载失败，可手动下载报表，然后继续用「选择文件」功能生成汇总。

## 环境检查

界面新增「检查环境」按钮，会检测：

- pandas 是否安装
- openpyxl 是否安装
- playwright 是否安装
- customtkinter 是否安装
- fastapi / uvicorn 是否安装
- Playwright Chromium 是否安装
- `data/database`、`data/output`、`data/raw`、`logs` 目录是否存在

也可以命令行执行：

```bash
python scripts/check_environment.py
```

## Demo 自检

安装依赖后可执行：

```bash
python scripts/demo_test.py
```

该脚本会使用 `samples/` 中的最小样例文件执行完整链路：

1. 初始化 SQLite 数据库。
2. 导入商品基础信息。
3. 生成 SPU 数据汇总和 SKU 数据汇总。
4. 校验两个工作表字段顺序。
5. 校验 SKU 类目排名补充。
6. 导出 Excel 到 `data/output/`。
7. 将汇总结果写入数据库。

输出 `demo ok` 表示本地 Excel 汇总和数据库链路可运行。

## 真实样例文件验证

如果仓库根目录存在以下 5 个真实 Excel 文件：

1. `SPU与SKU数据汇总表.xlsx`
2. `艺颂-2026-06-09-搜索分析-排名定位-商品排名.xlsx`
3. `艺颂-sku14975660_商品明细_离线_不包括对比时间_分天下载_2026-06-01_2026-06-09_zs6xkj7L.xlsx`
4. `艺颂-spu-14975660_商品明细_离线_不包括对比时间_分天下载_2026-06-01_2026-06-09_jWaAjl2a.xlsx`
5. `艺颂-衍生商品普通POP-SKU信息.xlsx`

请用户手动把这 5 个真实 Excel 文件放入 `jd_report_tool/samples/` 或仓库根目录。Codex PR 不提交、移动或生成 Excel 二进制文件（包括 `.xlsx` / `.xls`）；真实样例由你在 GitHub 或本地自行管理。运行下面命令时，测试脚本只会检测这两个位置，找到完整真实样例后再验证完整链路：

```bash
python scripts/demo_test.py
```

真实样例测试会检查：

- 商品明细 SPU 表是否可读取。
- 商品明细 SKU 表是否可读取。
- 商品排名定位表是否可读取。
- 商品基础信息表是否可读取并导入 SQLite。
- 是否参考 `SPU与SKU数据汇总表.xlsx` 的 `SPU数据汇总` / `SKU数据汇总` 字段结构。
- 是否生成 `SPU数据汇总` 和 `SKU数据汇总`。
- SKU 是否至少部分匹配到 SPU 和货号；未匹配的数据会保留并写入日志。
- 商品排名定位是否按 `SKU + 日期` 补充类目排名。
- 是否写入 `daily_spu_data` 和 `daily_sku_data`。
- 是否输出最终 Excel：`data/output/艺颂_SPU与SKU数据汇总_2026-06-01_2026-06-09.xlsx`。

如果真实样例不完整，脚本会列出缺失文件并提示你手动放入 `jd_report_tool/samples/` 或仓库根目录；脚本不会自动创建或提交任何 Excel 文件。
