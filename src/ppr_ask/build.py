"""Build the DuckDB database from the official PPR-ALL.zip.

The register is a cp1252 CSV with nine fields. Each row is kept verbatim in `sales_raw`;
`sales` holds the typed values the agent queries. Rules ported from ppr-map:
- every repeat of an earlier row's date, address and price is flagged a possible duplicate;
- bulk / portfolio sales (one total or averaged price filed against every unit) are
  flagged with ppr-map's D-031 rule, so medians are not skewed by them.
"""

from __future__ import annotations

import csv
import io
import tempfile
import urllib.request
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import duckdb

PPR_URL = (
    "https://www.propertypriceregister.ie/website/npsra/ppr/npsra-ppr.nsf/"
    "Downloads/PPR-ALL.zip/$FILE/PPR-ALL.zip"
)
ENCODING = "cp1252"
HEADER = [
    "Date of Sale (dd/mm/yyyy)",
    "Address",
    "County",
    "Eircode",
    "Price (€)",
    "Not Full Market Price",
    "VAT Exclusive",
    "Description of Property",
    "Property Size Description",
]
RAW_COLUMNS = [
    "raw_date",
    "raw_address",
    "raw_county",
    "raw_eircode",
    "raw_price",
    "raw_not_full_market_price",
    "raw_vat_exclusive",
    "raw_description",
    "raw_size",
]

# Bulk rule (ppr-map D-031): same date and price, and either
# - price above EUR 5M with at least 2 properties, in any county; or
# - at least 3 properties in one county, with a price that is not a whole EUR 1,000
#   or at least 3 of them in the same locality.
BIG_PRICE = 5_000_000
MIN_PROPERTIES = 3
MIN_SAME_LOCALITY = 3


class HeaderMismatchError(ValueError):
    """The upstream layout changed; stop rather than load the wrong columns."""


def download(dest: Path, url: str = PPR_URL) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "ppr-ask (open-source PPR tool)"})
    with urllib.request.urlopen(req, timeout=120) as resp, dest.open("wb") as out:
        out.write(resp.read())
    return dest


def read_csv_bytes(source: Path) -> bytes:
    """Return the CSV bytes from PPR-ALL.zip or a bare .csv."""
    if source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if len(names) != 1:
                raise ValueError(f"expected one CSV in {source}, found {names}")
            return zf.read(names[0])
    return source.read_bytes()


def to_utf8_rows(data: bytes) -> list[list[str]]:
    """Decode cp1252, check the header and return data rows with a 1-based line number."""
    reader = csv.reader(io.StringIO(data.decode(ENCODING), newline=""))
    header = next(reader, None)
    if header != HEADER:
        raise HeaderMismatchError(f"unexpected PPR header: {header!r}")
    rows = []
    for fields in reader:
        rows.append([str(reader.line_num), *fields])
    return rows


TRANSFORM = """
CREATE OR REPLACE TABLE sales AS
WITH t AS (
    SELECT
        line_no AS sale_id,
        try_strptime(trim(raw_date), '%d/%m/%Y')::DATE AS sale_date,
        regexp_replace(trim(raw_address), '\\s+', ' ', 'g') AS address,
        trim(raw_county) AS county,
        nullif(upper(regexp_replace(trim(raw_eircode), '\\s+', '', 'g')), '') AS eircode,
        try_cast(replace(regexp_extract(trim(raw_price), '^€([0-9,]+\\.[0-9]{2})$', 1), ',', '')
                 AS DECIMAL(14, 2)) AS price_eur,
        trim(raw_not_full_market_price) = 'Yes' AS not_full_market_price,
        trim(raw_vat_exclusive) = 'Yes' AS vat_exclusive,
        CASE
            WHEN lower(raw_description) LIKE '%second-hand%'
              OR lower(raw_description) LIKE '%athláimhe%'
              OR lower(raw_description) LIKE '%atháimhe%' THEN FALSE
            WHEN lower(trim(raw_description)) LIKE 'new %'
              OR lower(trim(raw_description)) LIKE '% nua' THEN TRUE
        END AS is_new,
        CASE
            WHEN trim(raw_size) = '' THEN NULL
            WHEN raw_size LIKE '%38%' AND raw_size LIKE '%125%' THEN '38 to 125 sq m'
            WHEN raw_size LIKE '%125%' THEN '125 sq m or more'
            WHEN raw_size LIKE '%38%' THEN 'under 38 sq m'
        END AS size_band
    FROM sales_raw
)
SELECT
    sale_id, sale_date,
    year(sale_date) AS sale_year,
    month(sale_date) AS sale_month,
    address, county, eircode,
    CASE WHEN length(eircode) = 7 THEN left(eircode, 3) END AS eircode_routing_key,
    price_eur, not_full_market_price, vat_exclusive, is_new, size_band,
    count(*) OVER (
        PARTITION BY sale_date, lower(address), price_eur ORDER BY sale_id
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) > 1 AS is_possible_duplicate,
    FALSE AS is_bulk,
    NULL::INTEGER AS bulk_group_size
FROM t
WHERE sale_date IS NOT NULL AND price_eur IS NOT NULL AND is_new IS NOT NULL
"""

BULK = f"""
CREATE TEMP TABLE bulk_member AS
WITH a AS (
    SELECT sale_id, sale_date, price_eur, lower(county) AS county,
           lower(address) AS property_key,
           regexp_split_to_array(lower(address), '\\s*,\\s*') AS parts
    FROM sales
),
b AS (
    SELECT *, len(parts) AS n FROM a
),
c AS (
    SELECT sale_id, sale_date, price_eur, county, property_key,
           CASE WHEN (parts[n] LIKE 'co %' OR parts[n] = county) AND n > 1
                THEN parts[n - 1] ELSE parts[n] END AS locality
    FROM b
),
big AS (
    SELECT sale_date, price_eur FROM c WHERE price_eur > {BIG_PRICE}
    GROUP BY ALL HAVING count(DISTINCT property_key) >= 2
),
grp AS (
    SELECT sale_date, price_eur, county FROM c
    GROUP BY ALL HAVING count(DISTINCT property_key) >= {MIN_PROPERTIES}
),
loc AS (
    SELECT sale_date, price_eur, county, max(k) AS max_same_locality
    FROM (SELECT sale_date, price_eur, county, locality, count(DISTINCT property_key) AS k
          FROM c JOIN grp USING (sale_date, price_eur, county) GROUP BY ALL)
    GROUP BY ALL
),
cty AS (
    SELECT sale_date, price_eur, county FROM grp JOIN loc USING (sale_date, price_eur, county)
    WHERE price_eur % 1000 <> 0 OR max_same_locality >= {MIN_SAME_LOCALITY}
),
members AS (
    SELECT c.sale_id, c.sale_date, c.price_eur, '*' AS scope
    FROM c JOIN big USING (sale_date, price_eur)
    UNION ALL
    SELECT c.sale_id, c.sale_date, c.price_eur, c.county
    FROM c JOIN cty USING (sale_date, price_eur, county)
    WHERE NOT EXISTS (SELECT 1 FROM big WHERE big.sale_date = c.sale_date
                                          AND big.price_eur = c.price_eur)
),
sized AS (
    SELECT sale_id, count(*) OVER (PARTITION BY sale_date, price_eur, scope) AS group_size
    FROM members
)
SELECT sale_id, group_size FROM sized
"""

MARK_BULK = """
UPDATE sales SET is_bulk = TRUE, bulk_group_size = bulk_member.group_size
FROM bulk_member WHERE sales.sale_id = bulk_member.sale_id
"""

VIEWS = """
CREATE OR REPLACE VIEW market_sales AS
SELECT * FROM sales
WHERE NOT not_full_market_price AND NOT is_bulk AND NOT is_possible_duplicate;
"""


def build(source: Path, db_path: Path) -> dict[str, object]:
    """Load `source` (PPR-ALL.zip or .csv) into a fresh DuckDB file at `db_path`."""
    rows = to_utf8_rows(read_csv_bytes(source))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_db = db_path.with_suffix(".tmp.duckdb")
    tmp_db.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        utf8 = Path(tmp) / "ppr.csv"
        with utf8.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["line_no", *RAW_COLUMNS])
            w.writerows(rows)
        con = duckdb.connect(str(tmp_db))
        try:
            columns = ", ".join(
                ["'line_no': 'INTEGER'"] + [f"'{c}': 'VARCHAR'" for c in RAW_COLUMNS]
            )
            con.execute(
                "CREATE TABLE sales_raw AS SELECT line_no, "
                + ", ".join(RAW_COLUMNS)
                + f" FROM read_csv(?, header = true, columns = {{{columns}}}, "
                "quote = '\"', escape = '\"')",
                [str(utf8)],
            )
            con.execute(TRANSFORM)
            con.execute(BULK)
            con.execute(MARK_BULK)
            con.execute(VIEWS)
            stats = dict(
                zip(
                    ["rows_in_file", "sales", "rejected", "bulk", "duplicates", "first", "last"],
                    con.execute(
                        """SELECT (SELECT count(*) FROM sales_raw), count(*),
                                  (SELECT count(*) FROM sales_raw) - count(*),
                                  count(*) FILTER (is_bulk),
                                  count(*) FILTER (is_possible_duplicate),
                                  min(sale_date), max(sale_date) FROM sales"""
                    ).fetchone()
                    or (),
                    strict=True,
                )
            )
            con.execute(
                "CREATE TABLE dataset_info AS SELECT ? AS source_file, ? AS built_at, "
                "?::DATE AS first_sale_date, ?::DATE AS last_sale_date",
                [
                    source.name,
                    datetime.now(UTC).isoformat(timespec="seconds"),
                    stats["first"],
                    stats["last"],
                ],
            )
            con.execute("CHECKPOINT")
        finally:
            con.close()
    tmp_db.replace(db_path)
    return stats
