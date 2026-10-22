from enum import StrEnum
from uuid import UUID

import yaml

from ...core.errors import ApplicationError
from ...core.project_context import DEFAULT_PROJECT_ID
from ..algorithms.domain.repositories import AlgorithmRepository


class DeploymentBackend(StrEnum):
    DOCKER = "docker"
    BENTOML = "bentoml"
    KSERVE = "kserve"


class DeploymentManifestService:
    def __init__(
        self,
        algorithms: AlgorithmRepository,
        project_id: UUID = DEFAULT_PROJECT_ID,
    ) -> None:
        self._algorithms = algorithms
        self._project_id = project_id

    def generate(self, algorithm_id: UUID, backend: DeploymentBackend) -> dict[str, str]:
        algorithm = self._algorithms.get(algorithm_id)
        if algorithm is None or algorithm.project_id != self._project_id:
            raise ApplicationError("ALGORITHM_NOT_FOUND", "算法版本不存在", 404)
        if backend is DeploymentBackend.DOCKER:
            return {
                "compose.yaml": yaml.safe_dump(
                    {
                        "services": {
                            algorithm.manifest.id: {
                                "image": algorithm.image,
                                "read_only": True,
                                "security_opt": ["no-new-privileges:true"],
                                "cap_drop": ["ALL"],
                            }
                        }
                    },
                    sort_keys=False,
                    allow_unicode=True,
                )
            }
        if backend is DeploymentBackend.BENTOML:
            return self._bentoml(algorithm)
        return self._kserve(algorithm)

    @staticmethod
    def _bentoml(algorithm) -> dict[str, str]:
        manifest = algorithm.manifest
        bentofile = {
            "service": "bento_service:CVAlgorithmService",
            "description": manifest.description,
            "labels": {
                "algorithm_id": manifest.id,
                "version": manifest.version,
                "task_type": manifest.task_type.value,
            },
            "include": ["*.py", ".platform/**", "manifest.yaml", "weights/**"],
            "python": {
                "requirements_txt": "requirements.txt",
                "packages": ["bentoml>=1.4,<2", "fastapi>=0.116,<1"],
            },
        }
        return {
            "bentofile.yaml": yaml.safe_dump(
                bentofile,
                sort_keys=False,
                allow_unicode=True,
            ),
            "bento_service.py": (
                "import bentoml\n"
                "from service import app\n\n"
                "@bentoml.service(traffic={'timeout': 120})\n"
                "@bentoml.asgi_app(app, path='/')\n"
                "class CVAlgorithmService:\n"
                "    pass\n"
            ),
            "BUILD.txt": (
                "bentoml build -f bentofile.yaml\n"
                "bentoml containerize <bento-tag>\n"
            ),
        }

    @staticmethod
    def _kserve(algorithm) -> dict[str, str]:
        name = f"cv-{algorithm.manifest.id}-{algorithm.manifest.version.replace('.', '-')}"
        manifest = {
            "apiVersion": "serving.kserve.io/v1beta1",
            "kind": "InferenceService",
            "metadata": {
                "name": name,
                "labels": {"cv.platform/managed": "true"},
                "annotations": {
                    "serving.kserve.io/deploymentMode": "RawDeployment",
                    "serving.kserve.io/autoscalerClass": "hpa",
                },
            },
            "spec": {
                "predictor": {
                    "minReplicas": 0,
                    "maxReplicas": 10,
                    "containers": [
                        {
                            "name": "algorithm",
                            "image": algorithm.image,
                            "ports": [{"containerPort": algorithm.internal_port}],
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": True,
                                "runAsNonRoot": True,
                                "runAsUser": 10001,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "resources": {
                                "requests": {
                                    "cpu": "500m",
                                    "memory": f"{algorithm.manifest.runtime.min_memory_mb}Mi",
                                },
                                "limits": {
                                    "cpu": "4",
                                    "memory": f"{algorithm.manifest.runtime.min_memory_mb}Mi"
                                }
                            },
                            "readinessProbe": {
                                "httpGet": {"path": "/health", "port": algorithm.internal_port}
                            },
                        }
                    ]
                }
            },
        }
        return {
            "inferenceservice.yaml": yaml.safe_dump(
                manifest,
                sort_keys=False,
                allow_unicode=True,
            ),
            "DEPLOY.txt": (
                "kubectl apply -f inferenceservice.yaml\n"
                "kubectl wait --for=condition=Ready inferenceservice/"
                f"{name} --timeout=300s\n"
            ),
        }
