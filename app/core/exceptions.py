from typing import Any, Dict, Optional
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from app.core.logging import logger


class FlowGridException(Exception):
    """Base exception class for FlowGrid backend domain errors."""

    def __init__(
        self,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class EntityNotFoundException(FlowGridException):
    """Raised when an inventory item, node, plan, or scenario is not found."""

    def __init__(self, entity_name: str, entity_id: Any):
        super().__init__(
            message=f"{entity_name} with identifier '{entity_id}' not found.",
            status_code=status.HTTP_404_NOT_FOUND,
            details={"entity": entity_name, "identifier": str(entity_id)},
        )


class AuthoritativeRuleViolationException(FlowGridException):
    """Raised when an allocation violates authoritative inventory balances or constraints."""

    def __init__(self, message: str, violations: Optional[list] = None):
        super().__init__(
            message=message,
            status_code=getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", 422),
            details={"violations": violations or []},
        )


class UnauthorizedCommitException(FlowGridException):
    """
    Raised when an attempt is made to commit an unvalidated plan or
    when an external entity attempts to bypass authoritative backend validation.
    """

    def __init__(self, message: str = "Only validated plans can be committed by the backend authority."):
        super().__init__(
            message=message,
            status_code=status.HTTP_403_FORBIDDEN,
            details={"rule": "authoritative_backend_commitment_required"},
        )


class DatabaseConnectionException(FlowGridException):
    """Raised when the database cannot be reached."""

    def __init__(self, message: str = "Authoritative database is currently unreachable."):
        super().__init__(
            message=message,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            details={"service": "database"},
        )


class ConflictException(FlowGridException):
    """Raised when an entity cannot be created or updated due to unique constraint or conflicting state."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            status_code=status.HTTP_409_CONFLICT,
            details=details or {},
        )


class InventoryVersionMismatchException(FlowGridException):
    """Raised when a candidate plan was calculated using an outdated inventory version."""

    def __init__(self, expected_version: int, current_version: int, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=(
                f"Inventory version mismatch: plan was calculated using inventory version {expected_version}, "
                f"but authoritative inventory is currently at version {current_version}."
            ),
            status_code=status.HTTP_409_CONFLICT,
            details={"expected_version": expected_version, "current_version": current_version, **(details or {})},
        )


class InsufficientInventoryException(AuthoritativeRuleViolationException):
    """Raised when requested allocation quantity exceeds available inventory."""

    def __init__(self, message: str, violations: Optional[list] = None):
        super().__init__(message=message, violations=violations or [])


class ExpiredBatchException(AuthoritativeRuleViolationException):
    """Raised when an allocation targets or includes expired inventory."""

    def __init__(self, message: str, violations: Optional[list] = None):
        super().__init__(message=message, violations=violations or [])


class PlanAlreadyCommittedException(ConflictException):
    """Raised when attempting to commit a plan that has already been authoritatively committed."""

    def __init__(self, plan_code: str):
        super().__init__(
            message=f"Plan '{plan_code}' has already been committed.",
            details={"plan_code": plan_code},
        )


class DecisionServiceException(FlowGridException):
    """Raised when the decision service is unreachable, returns 5xx, or fails to generate an output."""

    def __init__(
        self,
        message: str = "Decision service is currently unavailable.",
        details: Optional[Dict[str, Any]] = None,
        status_code: int = status.HTTP_502_BAD_GATEWAY,
    ):
        super().__init__(
            message=message,
            status_code=status_code,
            details=details or {},
        )


class DecisionServiceTimeoutException(DecisionServiceException):
    """Raised when the decision service request times out."""

    def __init__(self, message: str = "Decision service request timed out.", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            details=details or {},
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        )


class MalformedDecisionCandidateException(DecisionServiceException):
    """Raised when the decision service returns invalid, incomplete, or unparseable candidate data."""

    def __init__(self, message: str = "Decision service returned malformed candidate payload.", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            details=details or {},
            status_code=status.HTTP_502_BAD_GATEWAY,
        )


class InvalidStateTransitionException(AuthoritativeRuleViolationException):
    """Raised when an illegal plan lifecycle transition is requested."""

    def __init__(self, current_status: str, target_status: str):
        super().__init__(
            message=f"Invalid plan state transition from '{current_status}' to '{target_status}'.",
            violations=[{"current_status": current_status, "target_status": target_status}],
        )


def register_exception_handlers(app: FastAPI) -> None:
    """Registers centralized, secure exception handlers on the FastAPI application."""

    @app.exception_handler(FlowGridException)
    async def flowgrid_exception_handler(request: Request, exc: FlowGridException) -> JSONResponse:
        logger.warning(
            "Domain exception: %s | path: %s | details: %s",
            exc.message,
            request.url.path,
            exc.details,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": exc.__class__.__name__,
                "message": exc.message,
                "details": exc.details,
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        logger.warning("Request schema validation failure on %s", request.url.path)
        return JSONResponse(
            status_code=getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", 422),
            content={
                "error": "RequestValidationError",
                "message": "Input validation error in request payload.",
                "details": exc.errors(),
            },
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # Log error securely without exposing credentials or internal trace to client
        logger.error(
            "Unhandled server error on %s: %s",
            request.url.path,
            str(exc),
            exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "InternalServerError",
                "message": "An unexpected error occurred. The incident has been logged.",
            },
        )
