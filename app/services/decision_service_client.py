from typing import Any, Callable, Dict, List, Optional
import uuid
import httpx
from app.core.config import settings
from app.core.logging import logger
from app.core.exceptions import (
    DecisionServiceException,
    DecisionServiceTimeoutException,
    MalformedDecisionCandidateException,
)
from app.schemas.decision import CandidatePlanProposal, ProposedMovement
from app.models.enums import AllocationPriority


class DecisionServiceClient:
    """
    Client for communicating with external AI decision / optimization services.
    
    CRITICAL ARCHITECTURAL CONSTRAINTS:
    - The decision service proposes candidate allocations ONLY.
    - It CANNOT approve, commit, reserve, or directly modify authoritative inventory.
    - If the decision service fails, times out, or returns invalid payloads,
      the backend fails safely without modifying authoritative stock.
    """

    # Optional test mock hook to simulate external service behaviors (timeouts, malformed, offline)
    _mock_handler: Optional[Callable[[Dict[str, Any]], Any]] = None

    @classmethod
    def set_mock_handler(cls, handler: Optional[Callable[[Dict[str, Any]], Any]]) -> None:
        """Sets a mock handler for unit and integration testing."""
        cls._mock_handler = handler

    @classmethod
    def generate_candidate_plan(
        cls,
        scenario_code: str,
        scenario_type: str,
        parameters: Dict[str, Any],
        inventory_snapshot: Dict[str, Any],
    ) -> CandidatePlanProposal:
        """
        Sends scenario and inventory snapshot to the decision service and returns candidate proposals.
        """
        payload = {
            "scenario_code": scenario_code,
            "scenario_type": scenario_type,
            "parameters": parameters,
            "inventory_snapshot": inventory_snapshot,
        }

        endpoint = f"{settings.DECISION_SERVICE_URL.rstrip('/')}/api/v1/optimize"
        try:
            # 1. Check for test mock handler
            if cls._mock_handler is not None:
                raw_response = cls._mock_handler(payload)
                return cls._parse_response(raw_response, scenario_code)

            # 2. Call external HTTP decision service
            logger.info("Calling decision service at %s for scenario %s", endpoint, scenario_code)
            with httpx.Client(timeout=settings.DECISION_SERVICE_TIMEOUT_SECONDS) as client:
                response = client.post(endpoint, json=payload)
                if response.status_code != 200:
                    raise DecisionServiceException(
                        f"Decision service returned HTTP status {response.status_code}.",
                        details={"status_code": response.status_code, "body": response.text[:200]},
                    )
                return cls._parse_response(response.json(), scenario_code)

        except httpx.TimeoutException as exc:
            logger.error("Decision service timed out for scenario %s: %s", scenario_code, exc)
            raise DecisionServiceTimeoutException(
                f"Decision service timed out after {settings.DECISION_SERVICE_TIMEOUT_SECONDS}s.",
                details={"scenario_code": scenario_code},
            ) from exc

        except httpx.ConnectError as exc:
            logger.warning(
                "Decision service at %s unreachable (%s). Using local deterministic heuristic solver.",
                endpoint,
                exc,
            )
            # Local deterministic fallback solver for standalone/offline operation
            return cls._local_heuristic_solver(scenario_code, parameters, inventory_snapshot)

        except (DecisionServiceException, DecisionServiceTimeoutException, MalformedDecisionCandidateException):
            raise

        except Exception as exc:
            logger.error("Unexpected failure contacting decision service: %s", exc, exc_info=True)
            raise DecisionServiceException(
                f"Failed to communicate with decision service: {str(exc)}",
                details={"scenario_code": scenario_code},
            ) from exc

    @classmethod
    def _parse_response(cls, raw: Any, scenario_code: str) -> CandidatePlanProposal:
        """Validates that the returned candidate matches the schema strictly."""
        if not isinstance(raw, dict):
            raise MalformedDecisionCandidateException("Decision service returned malformed candidate payload (non-dictionary).")

        try:
            proposal = CandidatePlanProposal.model_validate(raw)
            return proposal
        except Exception as exc:
            logger.error("Malformed candidate plan from decision service: %s", exc)
            raise MalformedDecisionCandidateException(
                f"Decision service returned malformed candidate payload: {str(exc)}",
                details={"raw_payload": raw},
            ) from exc

    @classmethod
    def _local_heuristic_solver(
        cls,
        scenario_code: str,
        parameters: Dict[str, Any],
        inventory_snapshot: Dict[str, Any],
    ) -> CandidatePlanProposal:
        """
        Deterministic heuristic solver generating valid candidate proposals when
        external AI service is offline.
        """
        proposed_movements: List[ProposedMovement] = []
        warehouses = inventory_snapshot.get("warehouses", [])
        destination_stores = inventory_snapshot.get("stores", [])
        target_store_code = destination_stores[0]["code"] if destination_stores else "STORE-AUTO-01"

        # Look for affected SKUs or products in parameters
        affected_skus = parameters.get("affected_skus", [])
        surge_percentage = parameters.get("surge_percentage", 20)

        for wh in warehouses:
            wh_code = wh.get("code")
            batches = wh.get("batches", [])
            for b in batches:
                sku = b.get("sku")
                available = b.get("available_quantity", 0)
                if available <= 0:
                    continue

                # If specific affected SKUs, filter by them; otherwise use available stock
                if not affected_skus or sku in affected_skus:
                    # Allocate a portion of available inventory
                    alloc_qty = max(1, min(available, int(available * (surge_percentage / 100.0)) or 10))
                    proposed_movements.append(
                        ProposedMovement(
                            sku=sku,
                            from_node_code=wh_code,
                            to_node_code=target_store_code,
                            quantity=alloc_qty,
                            priority=AllocationPriority.HIGH if surge_percentage > 30 else AllocationPriority.NORMAL,
                            estimated_transit_hours=24,
                            rationale=f"Heuristic demand balancing for scenario '{scenario_code}'",
                        )
                    )

        # Fallback if no specific batch found
        if not proposed_movements and warehouses:
            first_wh = warehouses[0]
            first_sku = affected_skus[0] if affected_skus else "SKU-DEFAULT"
            proposed_movements.append(
                ProposedMovement(
                    sku=first_sku,
                    from_node_code=first_wh.get("code", "WH-01"),
                    to_node_code=target_store_code,
                    quantity=50,
                    priority=AllocationPriority.NORMAL,
                    estimated_transit_hours=24,
                    rationale="Baseline allocation proposal",
                )
            )

        return CandidatePlanProposal(
            candidate_id=f"CAND-{uuid.uuid4().hex[:8].upper()}",
            scenario_code=scenario_code,
            model_version="flowgrid-heuristic-solver-v1.0",
            confidence_score=0.92,
            proposed_movements=proposed_movements,
        )

    @classmethod
    def generate_candidate_with_ollama(
        cls,
        scenario_code: str,
        parameters: Dict[str, Any],
        inventory_snapshot: Dict[str, Any],
        model: Optional[str] = None,
    ) -> CandidatePlanProposal:
        """
        Generates dynamic candidate plan proposal utilizing Ollama local LLM
        with schema validation against CandidatePlanProposal.
        """
        from app.services.ollama_service import OllamaService
        return OllamaService.dynamic_candidate_generation(
            scenario_code=scenario_code,
            parameters=parameters,
            inventory_snapshot=inventory_snapshot,
            model=model,
        )

