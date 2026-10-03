"""TPC-H(dbgen) / TPC-DS(dsdgen) 생성 결과를 CRIM 학습 형식으로 변환한다.

- .tbl / .dat (헤더 없음, '|' 구분, 줄 끝 '|')  →  헤더가 있는 CSV
- 공식 DDL(dss.ddl, tpcds.sql)에서 컬럼명을, RI 정의(dss.ri, tpcds_ri.sql)에서 FK 를 읽어
  학습용 정답 파일(ground_truth_tpc.json)을 만든다.

    python scripts/prepare_tpc.py \
        --tpch-dir  /path/to/tpch-dbgen          # *.tbl, dss.ddl, dss.ri
        --tpcds-dir /path/to/tpcds-data          # *.dat
        --tpcds-tools /path/to/tpcds-kit/tools   # tpcds.sql, tpcds_ri.sql
        --out data/tpc

출력
    data/tpc/sf1/tpch_<table>.csv     TPC-H  (TPC-DS 의 customer 와 이름이 겹치지 않도록 접두어)
    data/tpc/tpcds/<table>.csv        TPC-DS
    data/tpc/train/*.csv              위 두 폴더의 합집합 (특징 추출용 컬럼 풀)
    data/tpc/ground_truth_tpc.json    {"sf1": {"fk_pairs": [...]}, "tpcds": {"fk_pairs": [...]}}
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
from pathlib import Path

TPCH_PREFIX = "tpch_"


def _strip_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def parse_columns(ddl: str) -> dict[str, list[str]]:
    """CREATE TABLE 문 → {table: [col, ...]} (소문자)"""
    tables = {}
    for m in re.finditer(r"create\s+table\s+(?:\w+\.)?(\w+)\s*\((.*?)\)\s*;", _strip_comments(ddl), re.I | re.S):
        cols = []
        depth, buf = 0, ""
        for ch in m.group(2) + ",":  # 괄호 안의 콤마(DECIMAL(15,2))는 무시
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if ch == "," and depth == 0:
                tok = buf.strip().split()
                if tok and tok[0].lower() not in ("primary", "constraint", "foreign", "unique"):
                    cols.append(tok[0].lower())
                buf = ""
            else:
                buf += ch
        tables[m.group(1).lower()] = cols
    return tables


def _cols(text: str) -> list[str]:
    return [c.strip().lower() for c in text.split(",")]


def parse_foreign_keys(ri: str) -> list[tuple[str, str, str, str]]:
    """RI 정의 → [(child_table, child_col, parent_table, parent_col)]

    TPC-H 형식:  ALTER TABLE TPCD.X ADD FOREIGN KEY NAME (cols) references TPCD.Y;   (참조 컬럼 = Y 의 PK)
    TPC-DS 형식: alter table x add constraint n foreign key (cols) references y (cols);
    """
    sql = _strip_comments(ri)
    pks = {
        m.group(1).lower(): _cols(m.group(2))
        for m in re.finditer(r"alter\s+table\s+(?:\w+\.)?(\w+)\s+add\s+primary\s+key\s*\(([^)]*)\)", sql, re.I)
    }
    fks = []
    pattern = (
        r"alter\s+table\s+(?:\w+\.)?(\w+)\s+add\s+(?:constraint\s+\w+\s+)?foreign\s+key\s*(?:\w+\s*)?"
        r"\(([^)]*)\)\s*references\s+(?:\w+\.)?(\w+)\s*(?:\(([^)]*)\))?"
    )
    for m in re.finditer(pattern, sql, re.I | re.S):
        child, child_cols, parent = m.group(1).lower(), _cols(m.group(2)), m.group(3).lower()
        parent_cols = _cols(m.group(4)) if m.group(4) else pks.get(parent, [])
        if len(parent_cols) != len(child_cols):
            raise ValueError(f"FK 컬럼 수 불일치: {m.group(0)}")
        fks += [(child, c, parent, p) for c, p in zip(child_cols, parent_cols, strict=True)]
    return fks


def convert(src: Path, dst: Path, columns: list[str], max_rows: int | None, append: bool = False) -> int:
    """'|' 구분 파일 → CSV. 줄 끝 '|' 로 생기는 빈 마지막 필드는 버린다.

    append=True 면 헤더 없이 이어 쓴다 (dsdgen -PARALLEL 로 나뉜 청크 파일).
    """
    n = 0
    mode = "a" if append else "w"
    with open(src, encoding="latin-1", newline="") as fin, open(dst, mode, encoding="utf-8", newline="") as fout:
        w = csv.writer(fout)
        if not append:
            w.writerow(columns)
        for line in fin:
            fields = line.rstrip("\r\n").split("|")
            if fields and fields[-1] == "":
                fields = fields[:-1]
            if len(fields) != len(columns):
                raise ValueError(f"{src.name}: 컬럼 {len(columns)}개 기대, {len(fields)}개 발견 (line {n + 1})")
            w.writerow(fields)
            n += 1
            if max_rows and n >= max_rows:
                break
    return n


def build(src_dir: Path, ext: str, ddl: Path, ri: Path, out_dir: Path, prefix: str, max_rows: int | None):
    columns = parse_columns(ddl.read_text(encoding="latin-1"))
    out_dir.mkdir(parents=True, exist_ok=True)
    produced = set()
    for f in sorted(src_dir.glob(f"*.{ext}")):
        table = re.sub(r"_\d+_\d+$", "", f.stem).lower()  # dsdgen 병렬 생성 시 접미사 제거
        if table not in columns:
            print(f"  skip {f.name} (DDL 에 없음)")
            continue
        rows = convert(f, out_dir / f"{prefix}{table}.csv", columns[table], max_rows, append=table in produced)
        produced.add(table)
        print(f"  {f.name:28} → {prefix}{table}.csv ({rows:,} rows)")
    fks = [
        {"child": [prefix + c, cc], "parent": [prefix + p, pc]}
        for c, cc, p, pc in parse_foreign_keys(ri.read_text(encoding="latin-1"))
        if c in produced and p in produced
    ]
    return fks


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tpch-dir", type=Path, help="dbgen 디렉터리 (*.tbl, dss.ddl, dss.ri)")
    ap.add_argument("--tpcds-dir", type=Path, help="dsdgen 출력 디렉터리 (*.dat)")
    ap.add_argument("--tpcds-tools", type=Path, help="tpcds-kit/tools (tpcds.sql, tpcds_ri.sql)")
    ap.add_argument("--out", type=Path, default=Path("data/tpc"))
    ap.add_argument("--max-rows", type=int, default=None, help="테이블당 최대 행 수 (기본: 전체)")
    args = ap.parse_args(argv)

    gt = {}
    if args.tpch_dir:
        print("[TPC-H]")
        gt["sf1"] = {
            "fk_pairs": build(
                args.tpch_dir, "tbl", args.tpch_dir / "dss.ddl", args.tpch_dir / "dss.ri",
                args.out / "sf1", TPCH_PREFIX, args.max_rows,
            )
        }  # fmt: skip
    if args.tpcds_dir:
        if not args.tpcds_tools:
            ap.error("--tpcds-dir 를 쓰려면 --tpcds-tools 도 필요합니다")
        print("[TPC-DS]")
        gt["tpcds"] = {
            "fk_pairs": build(
                args.tpcds_dir, "dat", args.tpcds_tools / "tpcds.sql", args.tpcds_tools / "tpcds_ri.sql",
                args.out / "tpcds", "", args.max_rows,
            )
        }  # fmt: skip
    if not gt:
        ap.error("--tpch-dir 또는 --tpcds-dir 중 하나는 필요합니다")

    train = args.out / "train"
    train.mkdir(parents=True, exist_ok=True)
    for sub in ("sf1", "tpcds"):
        for f in (args.out / sub).glob("*.csv") if (args.out / sub).exists() else []:
            shutil.copy2(f, train / f.name)

    (args.out / "ground_truth_tpc.json").write_text(json.dumps(gt, indent=2), encoding="utf-8")
    print({k: len(v["fk_pairs"]) for k, v in gt.items()}, "FK →", args.out / "ground_truth_tpc.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
