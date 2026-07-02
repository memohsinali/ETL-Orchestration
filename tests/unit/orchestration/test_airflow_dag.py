from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from types import SimpleNamespace


def _load_dag_module(module_name: str, dag_path: Path):
    assert dag_path.exists()

    spec = importlib.util.spec_from_file_location(module_name, dag_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_airflow_dag_imports():
    module = _load_dag_module(
        "etl_pipeline_dag",
        Path("airflow/dags/etl_pipeline_dag.py"),
    )

    dag = getattr(module, "dag", None)
    assert dag is not None
    assert dag.dag_id == "etl_pipeline_dag"
    # 3 extract + 3 validate + 3 transform + 3 load = 12 tasks
    assert len(dag.task_dict) == 12


def test_parallel_airflow_dag_imports():
    module = _load_dag_module(
        "etl_parallel_pipeline_dag",
        Path("airflow/dags/etl_parallel_pipeline_dag.py"),
    )

    dag = getattr(module, "dag", None)
    assert dag is not None
    assert dag.dag_id == "etl_parallel_pipeline_dag"
    # 3 extract + shared validate + shared transform + shared load = 6 tasks
    assert len(dag.task_dict) == 6


def test_parallel_validate_allows_empty_incremental_run(caplog):
    module = _load_dag_module(
        "etl_parallel_pipeline_dag_for_empty_validate",
        Path("airflow/dags/etl_parallel_pipeline_dag.py"),
    )

    def xcom_pull(task_ids):
        return []

    context = {"ti": SimpleNamespace(xcom_pull=xcom_pull)}

    with caplog.at_level(logging.INFO):
        result = module.validate_task(**context)

    assert result == []
    assert "No data to be extracted from csv_users" in caplog.text
    assert "No data to be extracted from api_products" in caplog.text
    assert "No data to be extracted from mongo_users" in caplog.text


def test_parallel_transform_and_load_allow_empty_incremental_run(caplog):
    module = _load_dag_module(
        "etl_parallel_pipeline_dag_for_empty_downstream",
        Path("airflow/dags/etl_parallel_pipeline_dag.py"),
    )

    def xcom_pull(task_ids):
        return []

    context = {"ti": SimpleNamespace(xcom_pull=xcom_pull)}

    with caplog.at_level(logging.INFO):
        assert module.transform_task(**context) == []
        assert module.load_task(**context) is None

    assert "No validated records to transform" in caplog.text
    assert "No transformed records to load" in caplog.text
