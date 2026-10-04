"""薄 HTTP 装配层；授权和数值计算留在可复用业务服务。"""

from uuid import UUID

from fastapi import Depends, Query, Request, Response
from sqlalchemy.orm import Session

from . import device_service as service
from .schemas import PredictionRequest


def register_device_routes(app, get_session, current, envelope):
    @app.get("/api/models")
    def models(request: Request, principal=Depends(current), session: Session = Depends(get_session)):
        return envelope(request, service.list_models(session, principal))

    @app.get("/api/engines")
    def engines(request: Request, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
                dataset_id: UUID | None = None, principal=Depends(current), session: Session = Depends(get_session)):
        return envelope(request, service.list_engines(session, principal, offset, limit, dataset_id))

    @app.get("/api/engines/{engine_key}/sensors")
    def sensors(request: Request, engine_key: str, end_cycle: int = Query(..., ge=1),
                start_cycle: int = Query(1, ge=1), columns: list[str] | None = Query(None),
                principal=Depends(current), session: Session = Depends(get_session)):
        return envelope(request, service.sensor_trends(session, principal, engine_key, end_cycle, start_cycle, columns))

    @app.post("/api/predictions", status_code=201)
    def predict(request: Request, response: Response, payload: PredictionRequest,
                principal=Depends(current), session: Session = Depends(get_session)):
        result, replayed = app.state.device_runtime.predict(session, principal, payload, request.state.request_id)
        session.commit()
        response.status_code = 200 if replayed else 201
        return envelope(request, {**result, "replayed": replayed})

    @app.get("/api/engines/{engine_key}/predictions")
    def history(request: Request, engine_key: str, end_cycle: int = Query(..., ge=1),
                offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
                principal=Depends(current), session: Session = Depends(get_session)):
        return envelope(request, service.prediction_history(session, principal, engine_key, end_cycle, offset, limit))
