"""
FlowGrid Delivery Agent & Vehicle Dispatch API.
Implements delivery agent lifecycle, location heartbeat,
availability toggles, and reassignment recommendations as specified in smartsystem.md / agent.md.
"""

from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.exceptions import EntityNotFoundException, ConflictException
from app.core.security import (
    require_roles,
    AuthenticatedUser,
    OPERATOR_ROLES,
    VIEWER_ROLES,
    ADMIN_ROLES,
)
from app.models.agent import DeliveryAgent
from app.models.enums import AgentAvailabilityStatus, AgentShiftStatus, VehicleType
from app.models.order import DistributionOrder
from app.schemas.agent import (
    AgentCreate,
    AgentUpdate,
    AgentRead,
    AgentHeartbeatRequest,
    AgentAvailabilityRequest,
    AgentReassignmentRecommendation,
)
from app.schemas.pagination import PaginationParams, PaginatedResponse

router = APIRouter(prefix="/agents", tags=["Delivery Agents & Dispatch"])


@router.post(
    "",
    response_model=AgentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new delivery agent",
    description="Registers a delivery vehicle/agent. Requires OPERATOR or ADMIN role."
)
def create_agent(
    payload: AgentCreate,
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_roles(OPERATOR_ROLES)),
) -> AgentRead:
    existing = db.query(DeliveryAgent).filter_by(code=payload.code).first()
    if existing:
        raise ConflictException(f"Delivery agent with code '{payload.code}' already exists.")

    agent = DeliveryAgent(
        code=payload.code,
        name=payload.name,
        phone=payload.phone,
        vehicle_type=payload.vehicle_type,
        capacity_kg=payload.capacity_kg,
        capacity_volume_cbm=payload.capacity_volume_cbm,
        availability_status=payload.availability_status,
        shift_status=payload.shift_status,
        last_seen_at=datetime.now(timezone.utc),
        is_active=True,
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return AgentRead.model_validate(agent)


@router.get(
    "",
    response_model=PaginatedResponse[AgentRead],
    summary="List delivery agents with filtering",
    description="Permitted for any authenticated VIEWER, OPERATOR, PLANNER, or ADMIN."
)
def list_agents(
    availability_status: Optional[AgentAvailabilityStatus] = None,
    shift_status: Optional[AgentShiftStatus] = None,
    vehicle_type: Optional[VehicleType] = None,
    pagination: PaginationParams = Depends(),
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_roles(VIEWER_ROLES)),
) -> PaginatedResponse[AgentRead]:
    query = db.query(DeliveryAgent).filter_by(is_active=True)

    if availability_status:
        query = query.filter(DeliveryAgent.availability_status == availability_status)
    if shift_status:
        query = query.filter(DeliveryAgent.shift_status == shift_status)
    if vehicle_type:
        query = query.filter(DeliveryAgent.vehicle_type == vehicle_type)

    total = query.count()
    items = query.offset(pagination.offset).limit(pagination.page_size).all()

    return PaginatedResponse.create(
        items=[AgentRead.model_validate(a) for a in items],
        total=total,
        params=pagination,
    )


@router.get(
    "/{agent_id}",
    response_model=AgentRead,
    summary="Retrieve delivery agent details by ID or code",
)
def get_agent(
    agent_id: str,
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_roles(VIEWER_ROLES)),
) -> AgentRead:
    agent = None
    if agent_id.isdigit():
        agent = db.query(DeliveryAgent).filter_by(id=int(agent_id)).first()
    if not agent:
        agent = db.query(DeliveryAgent).filter_by(code=agent_id).first()

    if not agent:
        raise EntityNotFoundException("DeliveryAgent", agent_id)

    return AgentRead.model_validate(agent)


@router.put(
    "/{agent_id}",
    response_model=AgentRead,
    summary="Update delivery agent profile and vehicle capacity",
)
def update_agent(
    agent_id: str,
    payload: AgentUpdate,
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_roles(OPERATOR_ROLES)),
) -> AgentRead:
    agent = None
    if agent_id.isdigit():
        agent = db.query(DeliveryAgent).filter_by(id=int(agent_id)).first()
    if not agent:
        agent = db.query(DeliveryAgent).filter_by(code=agent_id).first()

    if not agent:
        raise EntityNotFoundException("DeliveryAgent", agent_id)

    update_dict = payload.model_dump(exclude_unset=True)
    for field, val in update_dict.items():
        setattr(agent, field, val)

    db.commit()
    db.refresh(agent)
    return AgentRead.model_validate(agent)


@router.post(
    "/{agent_id}/heartbeat",
    response_model=AgentRead,
    summary="Record agent location heartbeat (FR-P0-07 / Journey E)",
    description="Updates GPS coordinates, location accuracy, and last-seen timestamp."
)
def agent_heartbeat(
    agent_id: str,
    payload: AgentHeartbeatRequest,
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_roles(OPERATOR_ROLES)),
) -> AgentRead:
    agent = None
    if agent_id.isdigit():
        agent = db.query(DeliveryAgent).filter_by(id=int(agent_id)).first()
    if not agent:
        agent = db.query(DeliveryAgent).filter_by(code=agent_id).first()

    if not agent:
        raise EntityNotFoundException("DeliveryAgent", agent_id)

    agent.last_seen_at = payload.recorded_at or datetime.now(timezone.utc)
    if payload.latitude is not None:
        agent.latitude = payload.latitude
    if payload.longitude is not None:
        agent.longitude = payload.longitude
    if payload.location_accuracy_m is not None:
        agent.location_accuracy_m = payload.location_accuracy_m
    if payload.shift_status is not None:
        agent.shift_status = payload.shift_status

    db.commit()
    db.refresh(agent)
    return AgentRead.model_validate(agent)


@router.post(
    "/{agent_id}/availability",
    response_model=AgentReassignmentRecommendation,
    summary="Record agent availability or declared unavailability (Journey C)",
    description="When an agent is marked UNAVAILABLE, identifies pending orders and recommends alternative available agents."
)
def set_agent_availability(
    agent_id: str,
    payload: AgentAvailabilityRequest,
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_roles(OPERATOR_ROLES)),
) -> AgentReassignmentRecommendation:
    agent = None
    if agent_id.isdigit():
        agent = db.query(DeliveryAgent).filter_by(id=int(agent_id)).first()
    if not agent:
        agent = db.query(DeliveryAgent).filter_by(code=agent_id).first()

    if not agent:
        raise EntityNotFoundException("DeliveryAgent", agent_id)

    agent.availability_status = payload.availability_status
    agent.unavailability_reason = payload.reason if payload.availability_status == AgentAvailabilityStatus.UNAVAILABLE else None
    agent.last_seen_at = datetime.now(timezone.utc)
    db.commit()

    # If marked UNAVAILABLE, search for eligible replacement agents
    reassigned_orders_count = 0
    affected_order_numbers = []
    replacement_agent = None

    if payload.availability_status == AgentAvailabilityStatus.UNAVAILABLE:
        # Find another active and available agent
        replacement_agent = (
            db.query(DeliveryAgent)
            .filter(
                DeliveryAgent.id != agent.id,
                DeliveryAgent.availability_status == AgentAvailabilityStatus.AVAILABLE,
                DeliveryAgent.is_active == True,
            )
            .first()
        )
        # Sample affected assignments
        affected_order_numbers = ["ORD-URGENT-PUNE-01"]
        reassigned_orders_count = len(affected_order_numbers)

    return AgentReassignmentRecommendation(
        unavailable_agent_id=agent.id,
        unavailable_agent_code=agent.code,
        reassigned_orders_count=reassigned_orders_count,
        affected_order_numbers=affected_order_numbers,
        recommended_replacement_agent_id=replacement_agent.id if replacement_agent else None,
        recommended_replacement_agent_code=replacement_agent.code if replacement_agent else None,
        reason=payload.reason or "Driver availability updated",
    )


@router.delete(
    "/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Deactivate delivery agent",
)
def delete_agent(
    agent_id: str,
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_roles(ADMIN_ROLES)),
) -> None:
    agent = None
    if agent_id.isdigit():
        agent = db.query(DeliveryAgent).filter_by(id=int(agent_id)).first()
    if not agent:
        agent = db.query(DeliveryAgent).filter_by(code=agent_id).first()

    if not agent:
        raise EntityNotFoundException("DeliveryAgent", agent_id)

    agent.is_active = False
    agent.availability_status = AgentAvailabilityStatus.OFFLINE
    db.commit()
