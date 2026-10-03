import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from prepare_tpc import main, parse_columns, parse_foreign_keys  # noqa: E402

TPCH_DDL = """
CREATE TABLE NATION  ( N_NATIONKEY INTEGER NOT NULL, N_NAME CHAR(25) NOT NULL,
                       N_REGIONKEY INTEGER NOT NULL, N_COMMENT VARCHAR(152));
CREATE TABLE REGION  ( R_REGIONKEY INTEGER NOT NULL, R_NAME CHAR(25) NOT NULL, R_COMMENT VARCHAR(152));
CREATE TABLE PARTSUPP ( PS_PARTKEY INTEGER NOT NULL, PS_SUPPKEY INTEGER NOT NULL,
                        PS_SUPPLYCOST DECIMAL(15,2) NOT NULL );
CREATE TABLE LINEITEM ( L_ORDERKEY INTEGER NOT NULL, L_PARTKEY INTEGER NOT NULL, L_SUPPKEY INTEGER NOT NULL );
"""
TPCH_RI = """
-- comment
ALTER TABLE TPCD.REGION ADD PRIMARY KEY (R_REGIONKEY);
ALTER TABLE TPCD.PARTSUPP ADD PRIMARY KEY (PS_PARTKEY,PS_SUPPKEY);
ALTER TABLE TPCD.NATION ADD FOREIGN KEY NATION_FK1 (N_REGIONKEY) references TPCD.REGION;
ALTER TABLE TPCD.LINEITEM
ADD FOREIGN KEY LINEITEM_FK2 (L_PARTKEY,L_SUPPKEY) references
        TPCD.PARTSUPP;
"""
TPCDS_RI = """
alter table call_center add constraint cc_d1 foreign key  (cc_closed_date_sk) references date_dim (d_date_sk);
-- alter table catalog_page add constraint cp_p foreign key  (cp_promo_id) references promotion (p_promo_sk);
"""


def test_parse_columns_ignores_type_commas():
    cols = parse_columns(TPCH_DDL)
    assert cols["partsupp"] == ["ps_partkey", "ps_suppkey", "ps_supplycost"]
    assert cols["nation"] == ["n_nationkey", "n_name", "n_regionkey", "n_comment"]


def test_parse_foreign_keys_tpch_pk_reference_and_composite():
    assert parse_foreign_keys(TPCH_RI) == [
        ("nation", "n_regionkey", "region", "r_regionkey"),
        ("lineitem", "l_partkey", "partsupp", "ps_partkey"),
        ("lineitem", "l_suppkey", "partsupp", "ps_suppkey"),
    ]


def test_parse_foreign_keys_tpcds_skips_comments():
    assert parse_foreign_keys(TPCDS_RI) == [("call_center", "cc_closed_date_sk", "date_dim", "d_date_sk")]


@pytest.fixture
def tpch_dir(tmp_path):
    d = tmp_path / "dbgen"
    d.mkdir()
    (d / "dss.ddl").write_text(TPCH_DDL)
    (d / "dss.ri").write_text(TPCH_RI)
    (d / "region.tbl").write_text("0|AFRICA|lar deposits|\n1|AMERICA|hs use|\n")
    (d / "nation.tbl").write_text("0|ALGERIA|0| haggle|\n1|ARGENTINA|1|al foxes|\n")
    return d


def test_end_to_end(tpch_dir, tmp_path):
    out = tmp_path / "tpc"
    assert main(["--tpch-dir", str(tpch_dir), "--out", str(out)]) == 0
    assert (out / "sf1" / "tpch_nation.csv").read_text().splitlines() == [
        "n_nationkey,n_name,n_regionkey,n_comment",
        "0,ALGERIA,0, haggle",
        "1,ARGENTINA,1,al foxes",
    ]
    assert (out / "train" / "tpch_region.csv").exists()
    gt = json.loads((out / "ground_truth_tpc.json").read_text())
    # 생성되지 않은 테이블(lineitem, partsupp)의 FK 는 제외
    assert gt == {
        "sf1": {"fk_pairs": [{"child": ["tpch_nation", "n_regionkey"], "parent": ["tpch_region", "r_regionkey"]}]}
    }


def test_column_count_mismatch_is_reported(tpch_dir, tmp_path):
    (tpch_dir / "region.tbl").write_text("0|AFRICA|\n")
    with pytest.raises(ValueError, match="region.tbl"):
        main(["--tpch-dir", str(tpch_dir), "--out", str(tmp_path / "x")])
