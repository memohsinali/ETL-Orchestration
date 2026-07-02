from __future__ import annotations

import importlib.util
from pathlib import Path


def test_airflow_dag_imports():
    dag_path = Path("airflow/dags/etl_pipeline_dag.py")
    assert dag_path.exists()

    spec = importlib.util.spec_from_file_location("etl_pipeline_dag", dag_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    dag = getattr(module, "dag", None)
    assert dag is not None
    assert dag.dag_id == "etl_pipeline_dag"
    # 3 extract + 3 validate + 3 transform + 3 load = 12 tasks
    assert len(dag.task_dict) == 12
