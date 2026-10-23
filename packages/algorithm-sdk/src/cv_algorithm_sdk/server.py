from contextlib import asynccontextmanager
from threading import Lock

from fastapi import FastAPI, HTTPException

from .algorithm import DetectionAlgorithm
from .protocol import HealthResponse, MetadataResponse, PredictRequest
from .results import AlgorithmResult


def create_algorithm_app(algorithm: DetectionAlgorithm) -> FastAPI:
    """Expose an algorithm through the platform's standard HTTP contract."""

    state = {"ready": False}
    prediction_lock = Lock()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        algorithm.load()
        state["ready"] = True
        yield
        state["ready"] = False

    app = FastAPI(
        title=algorithm.manifest.name,
        version=algorithm.manifest.version,
        lifespan=lifespan,
    )

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", ready=state["ready"])

    @app.get("/metadata", response_model=MetadataResponse)
    def metadata() -> MetadataResponse:
        manifest = algorithm.manifest
        return MetadataResponse(
            algorithm_id=manifest.id,
            version=manifest.version,
            task_type=manifest.task_type,
            input_types=manifest.input.media_types,
            output_type=manifest.output.type,
        )

    @app.get("/readiness")
    def readiness() -> dict:
        return {"ready": state["ready"], **getattr(algorithm, "readiness", {})}

    @app.post("/predict", response_model=AlgorithmResult)
    def predict(request: PredictRequest) -> AlgorithmResult:
        # Fail fast rather than accumulating an unbounded queue inside the container.
        if not prediction_lock.acquire(blocking=False):
            raise HTTPException(status_code=429, detail="算法实例忙，请稍后重试")
        try:
            return algorithm.predict(request)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            prediction_lock.release()

    return app
