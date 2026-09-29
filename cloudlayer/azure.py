"""Azure adapter. Implement upload/download/push_image for Lab 1.

SDK:  pip install azure-storage-blob azure-identity azure-containerregistry
Docs: BlobServiceClient for storage; ACR push goes through `docker push` after
      `az acr login --name <registry>`.

Hints for Lab 1:
  * BLOB_URI is either abfss://container@account.dfs.core.windows.net/prefix or
    https://account.blob.core.windows.net/container/prefix. Pick one form and parse
    it here, never in src/.
  * Use DefaultAzureCredential rather than a connection string. It picks up your CLI
    login locally and your managed identity in CI, which is what Lab 4 needs.
  * push_image must return the digest reference: registry.azurecr.io/repo@sha256:...
  * Azure tags live on the resource, not the blob. Tag the storage account, the
    registry, and later the workspace with cfg.tags(1).
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from urllib.parse import urlparse

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from azure.ai.ml import MLClient, Input, Output, command
from azure.ai.ml.entities import Environment, UserIdentityConfiguration

from cloudlayer.base import CloudAdapter


class AzureAdapter(CloudAdapter):
    def _blob_parts(self) -> tuple[str, str, str]:
        """Parse BLOB_URI into account URL, container, and prefix."""
        parsed = urlparse(self.cfg.blob_uri)

        if parsed.scheme != "https" or not parsed.netloc.endswith(
            ".blob.core.windows.net"
        ):
            raise ValueError(
                "BLOB_URI must use https://<account>.blob.core.windows.net/"
                "<container>/<prefix>"
            )

        account_url = f"{parsed.scheme}://{parsed.netloc}"
        parts = parsed.path.strip("/").split("/")

        if not parts or not parts[0]:
            raise ValueError("BLOB_URI must include a container")

        container = parts[0]
        prefix = "/".join(parts[1:]).strip("/")

        return account_url, container, prefix

    def upload(self, local_path: str, key: str) -> str:
        account_url, container, prefix = self._blob_parts()

        blob_name = "/".join(part for part in (prefix, key) if part)

        credential = DefaultAzureCredential()
        service = BlobServiceClient(
            account_url=account_url,
            credential=credential,
        )

        blob = service.get_blob_client(
            container=container,
            blob=blob_name,
        )

        with open(local_path, "rb") as file:
            blob.upload_blob(file, overwrite=True)

        return f"{account_url}/{container}/{blob_name}"

    def download(self, uri: str, local_path: str) -> None:
        parsed = urlparse(uri)

        if parsed.scheme != "https" or not parsed.netloc.endswith(
            ".blob.core.windows.net"
        ):
            raise ValueError(
                "Azure blob URI must use https://<account>.blob.core.windows.net/"
            )

        account_url = f"{parsed.scheme}://{parsed.netloc}"
        parts = parsed.path.strip("/").split("/")

        if len(parts) < 2:
            raise ValueError("Azure blob URI must include container and blob name")

        container = parts[0]
        blob_name = "/".join(parts[1:])

        credential = DefaultAzureCredential()
        service = BlobServiceClient(
            account_url=account_url,
            credential=credential,
        )

        blob = service.get_blob_client(
            container=container,
            blob=blob_name,
        )

        destination = Path(local_path)
        destination.parent.mkdir(parents=True, exist_ok=True)

        with open(destination, "wb") as file:
            blob.download_blob().readinto(file)

    def push_image(self, local_tag: str) -> str:
        registry_repo = self.cfg.container_registry.rstrip("/")

        registry_host, repo = registry_repo.split("/", 1)
        registry_name = registry_host.split(".", 1)[0]

        tag = local_tag.rsplit(":", 1)[1]
        remote_tag = f"{registry_repo}:{tag}"

        subprocess.run(
            ["az", "acr", "login", "--name", registry_name],
            check=True,
        )

        subprocess.run(
            ["docker", "tag", local_tag, remote_tag],
            check=True,
        )

        result = subprocess.run(
            ["docker", "push", remote_tag],
            check=True,
            text=True,
            capture_output=True,
        )

        output = result.stdout + result.stderr

        digest = None
        for line in output.splitlines():
            if "digest:" in line.lower():
                digest = line.split(":", 1)[1].strip()
                break

        if not digest or not digest.startswith("sha256:"):
            inspect = subprocess.run(
                [
                    "docker",
                    "image",
                    "inspect",
                    remote_tag,
                    "--format",
                    "{{index .RepoDigests 0}}",
                ],
                check=True,
                text=True,
                capture_output=True,
            )

            repo_digest = inspect.stdout.strip()

            if "@sha256:" not in repo_digest:
                raise RuntimeError(
                    f"Could not determine pushed image digest. Docker output:\n{output}"
                )

            digest = repo_digest.split("@", 1)[1]

        return f"{registry_repo}@{digest}"

    def _ml_client(self) -> MLClient:
        workspace = __import__("os").environ.get("AZURE_ML_WORKSPACE")
        if not workspace:
            raise RuntimeError("AZURE_ML_WORKSPACE is not set")

        credential = DefaultAzureCredential()
        return MLClient(
            credential=credential,
            subscription_id=__import__("os").environ["AZURE_SUBSCRIPTION_ID"],
            resource_group_name=self.cfg.project_id,
            workspace_name=workspace,
        )

    def submit_training(self, image_uri: str, args: dict[str, object]) -> str:
        """Submit an Azure ML command job for Lab 2."""
        ml_client = self._ml_client()

        input_uri = str(args.get("input_uri", self.cfg.blob_uri))
        output_uri = str(args["output_uri"])
        compute = str(args.get("compute", "itcs3556688020-cpu"))
        experiment_name = str(args.get("experiment", "itcs355-lab2"))

        job = command(
            display_name="itcs355-lab2-training",
            experiment_name=experiment_name,
            code=".",
            command=str(args["command"]),
            environment=Environment(
                image=image_uri,
                name="itcs355-lab2-env",
            ),
            compute=compute,
            inputs={
                "data": Input(
                    type="uri_file",
                    path=input_uri,
                    mode="download",
                )
            },
            outputs={
                "artifacts": Output(
                    type="uri_folder",
                    path=output_uri,
                    mode="rw_mount",
                )
            },
        )

        created = ml_client.jobs.create_or_update(job)
        return created.name

    def wait_training(self, job_id: str) -> dict[str, object]:
        import time

        ml_client = self._ml_client()

        while True:
            job = ml_client.jobs.get(job_id)
            status = str(job.status)

            print(f"Azure ML job {job_id}: {status}")

            if status.upper().endswith(("COMPLETED", "FAILED", "CANCELED", "NOT_RESPONDING")):
                break

            time.sleep(10)

        result = {
            "job_id": job.name,
            "status": status,
            "studio_url": getattr(job, "studio_url", None),
        }

        if not status.upper().endswith("COMPLETED"):
            raise RuntimeError(f"Azure ML training job failed: {result}")

        return result

    def register_model(self, model_uri: str, name: str) -> str:
        """Register an MLflow model in Azure ML and return its version."""
        from azure.ai.ml.constants import AssetTypes
        from azure.ai.ml.entities import Model

        ml_client = self._ml_client()

        model = Model(
            path=model_uri,
            name=name,
            type=AssetTypes.MLFLOW_MODEL,
        )

        registered = ml_client.models.create_or_update(model)
        return str(registered.version)

    # submit_training / register_model  -> Lab 2 (Azure ML command job + model registry)

    def deploy(self, model_ref: str, endpoint: str, instance: str) -> str:
        """Deploy the serving image to Azure Container Apps."""
        import os

        parts = instance.split(":")
        if len(parts) != 2:
            raise ValueError(
                "instance must be '<cpu>:<memory>', e.g. '0.25:0.5Gi'"
            )

        cpu, memory = parts

        command = [
            "az",
            "containerapp",
            "update",
            "--name",
            endpoint,
            "--resource-group",
            self.cfg.project_id,
            "--image",
            model_ref,
            "--cpu",
            cpu,
            "--memory",
            memory,
            "--set-env-vars",
            f"MODEL_REGISTRY_NAME={self.cfg.model_registry_name}",
            f"MODEL_VERSION={os.environ.get('MODEL_VERSION', '2')}",
            f"MLFLOW_TRACKING_URI={os.environ.get('MLFLOW_TRACKING_URI', self.cfg.mlflow_tracking_uri)}",
        ]

        subprocess.run(
            command,
            check=True,
            text=True,
            capture_output=True,
        )

        return endpoint

    def invoke(self, endpoint: str, payload: dict[str, object]) -> dict[str, object]:
        """Invoke the deployed Azure Container App."""
        import json
        import urllib.request

        url = endpoint.rstrip("/") + "/predict"

        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))

    def teardown(self, tags: dict[str, str]) -> list[str]:
        """Delete Azure resources matching all supplied tags."""
        import json

        result = subprocess.run(
            [
                "az",
                "resource",
                "list",
                "--resource-group",
                self.cfg.project_id,
                "-o",
                "json",
            ],
            check=True,
            text=True,
            capture_output=True,
        )
        resources = json.loads(result.stdout)

        matches = [
            resource
            for resource in resources
            if all((resource.get("tags") or {}).get(key) == value for key, value in tags.items())
        ]

        priority = {
            "Microsoft.App/containerApps": 0,
            "Microsoft.App/managedEnvironments": 1,
        }
        matches.sort(key=lambda resource: priority.get(resource.get("type"), 2))

        deleted = []
        for resource in matches:
            subprocess.run(
                [
                    "az",
                    "resource",
                    "delete",
                    "--ids",
                    resource["id"],
                    "--only-show-errors",
                ],
                check=True,
                text=True,
                capture_output=True,
            )
            deleted.append(resource["name"])

        return deleted

    # deploy / invoke                   -> Lab 3 (Azure Container Apps deployment)
    # emit_metric                       -> Lab 4 (Azure Monitor custom metric)
    # generate                          -> Lab 5 (managed LLM endpoint; read the usage block for tokens)
    # teardown                          -> Lab 5 (resource graph query by tag)
