from datetime import datetime, timezone
from typing import Any, Dict
from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.config import settings
from app.db.session import get_db

router = APIRouter(tags=["Health & Diagnostics"])


@router.get("/health", status_code=status.HTTP_200_OK)
def health_check() -> Dict[str, Any]:
    """
    Liveness probe: verifies the FastAPI application process is up and running.
    Never exposes internal secrets or connection credentials.
    """
    return {
        "status": "healthy",
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "authoritative_backend_authority": "ENFORCED",
        "direct_decision_commit_allowed": settings.ALLOW_DIRECT_DECISION_COMMIT,
    }


@router.get("/health/ready")
@router.get("/health/readiness")
@router.get("/ready")
def readiness_check(
    response: Response,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Readiness probe: validates database connectivity before accepting allocation traffic.
    Returns 200 OK when connected; returns 503 Service Unavailable when the database cannot be reached.
    Guarantees no database secrets are exposed in the output.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    try:
        db.execute(text("SELECT 1"))
        return {
            "status": "ready",
            "service": settings.PROJECT_NAME,
            "database": "connected",
            "timestamp": timestamp,
        }
    except Exception:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "unhealthy",
            "service": settings.PROJECT_NAME,
            "database": "disconnected",
            "message": "Authoritative database is currently unreachable.",
            "timestamp": timestamp,
        }
