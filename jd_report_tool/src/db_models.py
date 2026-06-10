SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS stores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_name TEXT,
    shop_id TEXT,
    platform TEXT DEFAULT 'JD',
    enabled INTEGER DEFAULT 1,
    created_at TEXT,
    updated_at TEXT,
    UNIQUE(shop_id)
);

CREATE TABLE IF NOT EXISTS products_spu (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER,
    spu TEXT,
    item_no TEXT,
    product_title TEXT,
    category TEXT,
    brand TEXT,
    status TEXT,
    main_sku TEXT,
    remark TEXT,
    created_at TEXT,
    updated_at TEXT,
    UNIQUE(store_id, spu)
);

CREATE TABLE IF NOT EXISTS products_sku (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER,
    spu TEXT,
    sku TEXT,
    item_no TEXT,
    sku_name TEXT,
    color TEXT,
    config TEXT,
    price REAL,
    cost REAL,
    stock INTEGER,
    is_main_sku INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT,
    UNIQUE(store_id, sku)
);

CREATE TABLE IF NOT EXISTS product_mapping (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER,
    spu TEXT,
    sku TEXT,
    item_no TEXT,
    bind_status TEXT DEFAULT 'active',
    created_at TEXT,
    updated_at TEXT,
    UNIQUE(store_id, sku)
);

CREATE TABLE IF NOT EXISTS report_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER,
    report_type TEXT,
    date_start TEXT,
    date_end TEXT,
    source_file_name TEXT,
    saved_file_path TEXT,
    download_time TEXT,
    status TEXT,
    error_msg TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS daily_spu_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT,
    store_id INTEGER,
    spu TEXT,
    item_no TEXT,
    exposure INTEGER,
    clicks INTEGER,
    ctr REAL,
    order_qty INTEGER,
    conversion_rate REAL,
    gmv REAL,
    created_at TEXT,
    updated_at TEXT,
    UNIQUE(date, store_id, spu)
);

CREATE TABLE IF NOT EXISTS daily_sku_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT,
    store_id INTEGER,
    spu TEXT,
    item_no TEXT,
    sku TEXT,
    exposure INTEGER,
    clicks INTEGER,
    ctr REAL,
    order_qty INTEGER,
    conversion_rate REAL,
    gmv REAL,
    category_rank INTEGER,
    created_at TEXT,
    updated_at TEXT,
    UNIQUE(date, store_id, sku)
);
"""

INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_mapping_store_spu ON product_mapping(store_id, spu)",
    "CREATE INDEX IF NOT EXISTS idx_daily_spu_store_date ON daily_spu_data(store_id, date)",
    "CREATE INDEX IF NOT EXISTS idx_daily_sku_store_date ON daily_sku_data(store_id, date)",
]
