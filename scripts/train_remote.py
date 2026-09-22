"""Submit and wait for an Azure ML training job for Lab 2."""
from __future__ import annotations

from src import config
from cloudlayer.factory import get_adapter


def main() -> None:
    cfg = config.load()
    adapter = get_adapter(cfg)

    image_uri = f"{cfg.container_registry}:lab2fix7"

    output_uri = (
        "abfss://itcs355@itcs3556688020.dfs.core.windows.net/"
        "itcs355/outputs/lab2"
    )

    command = (
        "cp ${{inputs.data}} /app/data/raw/sensors.csv && "
        "DATA_DIR=/app/data python -m src.train "
        "--seed 20260101 "
        "--experiment itcs355-lab2 "
        "--metrics-out /tmp/metrics.json && "
        "cp /tmp/metrics.json ${{outputs.artifacts}}/metrics.json"
    )

    job_id = adapter.submit_training(
        image_uri,
        {
            "input_uri": (
                "wasbs://itcs355@itcs3556688020.blob.core.windows.net/"
                "itcs355/raw/sensors.csv"
            ),
            "output_uri": output_uri,
            "compute": "itcs3556688020-cpu",
            "experiment": "itcs355-lab2",
            "command": command,
        },
    )

    print(f"Submitted Azure ML job: {job_id}")

    result = adapter.wait_training(job_id)
    print(result)


if __name__ == "__main__":
    main()