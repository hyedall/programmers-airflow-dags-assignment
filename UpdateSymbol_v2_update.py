from airflow import DAG
from airflow.decorators import task
from airflow.providers.postgres.hooks.postgres import PostgresHook
from datetime import datetime
from pandas import Timestamp

import yfinance as yf
import pandas as pd
import logging


def get_Redshift_connection(autocommit=True):
    hook = PostgresHook(postgres_conn_id='redshift_dev_db')
    conn = hook.get_conn()
    conn.autocommit = autocommit
    return conn.cursor()


@task
def get_historical_prices(symbol):
    ticket = yf.Ticker(symbol)
    data = ticket.history()
    records = []

    for index, row in data.iterrows():
        date = index.strftime('%Y-%m-%d %H:%M:%S')
        records.append([date, row["Open"], row["High"], row["Low"], row["Close"], row["Volume"]])

    return records


def _create_table(cur, schema, table, drop_first):
    if drop_first: # 있으면 삭제하고 
        cur.execute(f"DROP TABLE IF EXISTS {schema}.{table};")
    cur.execute(f"""
    CREATE TABLE IF NOT EXISTS {schema}.{table} (
    date date,
    "open" float,
    high float,
    low float,
    close float,
    volume bigint,
    created_date timestamp default GETDATE());""")


@task
def load(schema, table, records):
    logging.info("load started")
    cur = get_Redshift_connection()
    try:
        cur.execute("BEGIN;")
        # 원본 테이블이 없으면 생성 - 테이블이 처음 한번 만들어질 때 필요한 코드
        _create_table(cur, schema, table, False) # 있으면 남겨두고 생성
        # 임시 테이블로 원본 테이블을 복사 / 새 데이터 넣기
        cur.execute(f"CREATE TEMP TABLE t AS SELECT * FROM {schema}.{table};")
        for r in records:
            sql = f"INSERT INTO t VALUES ('{r[0]}', {r[1]}, {r[2]}, {r[3]}, {r[4]}, {r[5]});"
            print(sql)
            cur.execute(sql)

        # 원본 테이블 생성
        _create_table(cur, schema, table, True) # 삭제 후 생성 
        # 임시 테이블 내용을 원본 테이블로 복사
        # 방식을 ROW_NUMBER쓰려면 created_date를 추가해줘야함
        # cur.execute(f"INSERT INTO {schema}.{table} SELECT DISTINCT * FROM t;")
        alter_sql = f"""INSERT INTO {schema}.{table}
        SELECT date, "open", high, low, close, volume
        FROM(
            SELECT *,
            ROW_NUMBER() OVER (PARTITION BY date
            ORDER BY created_date DESC) seq
            FROM t
        )
        WHERE seq = 1;
        """
        cur.execute(alter_sql)
        cur.execute("COMMIT;")   # cur.execute("END;")
    except Exception as error:
        print(error)
        cur.execute("ROLLBACK;") 
        raise
    logging.info("load done")


with DAG(
    dag_id = 'UpdateSymbol_v2',
    start_date = datetime(2023,5,30),
    catchup=False,
    tags=['API'],
    schedule = '0 10 * * *'
    ) as dag:

    results = get_historical_prices("AAPL")
    load("dahye991228", "stock_info_v2", results)
