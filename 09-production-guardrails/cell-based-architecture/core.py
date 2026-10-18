"""
Cell-Based Architecture & Blast Radius Partitioning Engine
==========================================================
Domain 9: Production Guardrails & Governance ("Red Tape") - PoC 9.2

Production-grade implementation of Cell-Based (Cellular) Architecture, as used
by AWS (Route 53, DynamoDB), Slack, and Salesforce to prevent global cascading
outages and constrain blast radiuses to small, isolated customer cohorts.

Key Architecture Principles:
1. Strict Failure Domain Isolation: Each cell is an independent, self-contained
   deployment with its own compute, storage, and state.
2. Zero Cross-Cell Dependencies: Cells never communicate with each other in the data path.
3. Thin, Stateless Cell Router: Deterministic tenant-to-cell routing (Hash & Directory).
4. Blast Radius Containment: A catastrophic outage in Cell A affects at most 1/N
   of the fleet, with zero impact on remaining cells.
5. Control Plane & Live Evacuation: Non-blocking cell migration and noisy-neighbor isolation.
"""

from __future__ import annotations

import enum
import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("CellArchitecture")


class CellState(str, enum.Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DRAINING = "DRAINING"
    OFFLINE = "OFFLINE"


class TenantTier(str, enum.Enum):
    FREE = "FREE"
    STANDARD = "STANDARD"
    ENTERPRISE = "ENTERPRISE"


@dataclass
class Tenant:
    """Represents a customer/tenant identity mapped to a specific cell."""
    tenant_id: str
    name: str
    tier: TenantTier = TenantTier.STANDARD
    assigned_cell_id: Optional[str] = None
    is_migrating: bool = False
    migration_target_cell_id: Optional[str] = None


@dataclass
class CellRequest:
    """An incoming request targeting a specific tenant."""
    request_id: str
    tenant_id: str
    action: str
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


@dataclass
class CellResponse:
    """Result returned by a cell for a processed request."""
    request_id: str
    cell_id: str
    tenant_id: str
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    latency_ms: float = 0.0


class CellOutageException(Exception):
    """Raised when a request is dispatched to an offline or failed cell."""
    pass


class CellCapacityExceededException(Exception):
    """Raised when an incoming tenant placement exceeds cell capacity."""
    pass


class Cell:
    """
    Self-contained, isolated mini-deployment (Failure Domain).
    Holds its own dedicated data store, compute state, and concurrency locks.
    Never shares state with other cells.
    """
    def __init__(self, cell_id: str, max_tenants: int = 100, max_qps: int = 1000) -> None:
        self.cell_id = cell_id
        self.max_tenants = max_tenants
        self.max_qps = max_qps
        self.state = CellState.HEALTHY
        self._lock = threading.RLock()

        # Isolated in-cell state (simulating isolated DB/cache)
        self._tenant_records: Dict[str, Dict[str, Any]] = {}
        self._tenants_assigned: Set[str] = set()

        # Cell telemetry
        self.total_requests_processed = 0
        self.total_errors = 0
        self.simulated_failure_mode: Optional[str] = None

    def assign_tenant(self, tenant_id: str) -> None:
        with self._lock:
            if len(self._tenants_assigned) >= self.max_tenants:
                raise CellCapacityExceededException(
                    f"Cell '{self.cell_id}' reached capacity limit ({self.max_tenants} tenants)"
                )
            self._tenants_assigned.add(tenant_id)
            if tenant_id not in self._tenant_records:
                self._tenant_records[tenant_id] = {}

    def remove_tenant(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._tenants_assigned.discard(tenant_id)
            return self._tenant_records.pop(tenant_id, None)

    def import_tenant_data(self, tenant_id: str, data: Dict[str, Any]) -> None:
        with self._lock:
            self._tenants_assigned.add(tenant_id)
            self._tenant_records[tenant_id] = dict(data)

    def process_request(self, request: CellRequest) -> CellResponse:
        start_time = time.time()

        with self._lock:
            self.total_requests_processed += 1

            # 1. Failure Domain Isolation Check
            if self.state == CellState.OFFLINE or self.simulated_failure_mode:
                self.total_errors += 1
                latency = (time.time() - start_time) * 1000
                err_msg = f"Cell '{self.cell_id}' failure: {self.simulated_failure_mode or 'Cell is OFFLINE'}"
                return CellResponse(
                    request_id=request.request_id,
                    cell_id=self.cell_id,
                    tenant_id=request.tenant_id,
                    success=False,
                    error=err_msg,
                    latency_ms=latency,
                )

            # 2. Verify tenant data presence
            if request.tenant_id not in self._tenants_assigned:
                latency = (time.time() - start_time) * 1000
                return CellResponse(
                    request_id=request.request_id,
                    cell_id=self.cell_id,
                    tenant_id=request.tenant_id,
                    success=False,
                    error=f"Tenant '{request.tenant_id}' not found in cell '{self.cell_id}'",
                    latency_ms=latency,
                )

            # 3. Simulate stateful CRUD operation on cell's local store
            tenant_db = self._tenant_records[request.tenant_id]
            if request.action == "WRITE":
                key = request.payload.get("key", "default_key")
                val = request.payload.get("value")
                tenant_db[key] = val
                result_data = {"key": key, "value": val, "op": "WRITTEN"}
            elif request.action == "READ":
                key = request.payload.get("key", "default_key")
                val = tenant_db.get(key)
                result_data = {"key": key, "value": val, "op": "READ"}
            else:
                result_data = {"status": "ACK", "action": request.action}

            latency = (time.time() - start_time) * 1000
            return CellResponse(
                request_id=request.request_id,
                cell_id=self.cell_id,
                tenant_id=request.tenant_id,
                success=True,
                data=result_data,
                latency_ms=latency,
            )

    def inject_failure(self, failure_type: str = "DATABASE_CORRUPTION") -> None:
        """Inject a fatal in-cell failure (simulating DB crash, pod loop, etc.)."""
        with self._lock:
            self.state = CellState.OFFLINE
            self.simulated_failure_mode = failure_type

    def heal(self) -> None:
        """Restore cell back to healthy state."""
        with self._lock:
            self.state = CellState.HEALTHY
            self.simulated_failure_mode = None

    @property
    def tenant_count(self) -> int:
        with self._lock:
            return len(self._tenants_assigned)


class RoutingStrategy(str, enum.Enum):
    DIRECTORY_MAP = "DIRECTORY_MAP"
    HASH_RING = "HASH_RING"


class CellRouter:
    """
    Lightweight, stateless request router at the perimeter.
    Maps incoming tenant requests to their designated isolated cell.
    Supports both dynamic Directory mapping (with live migration)
    and deterministic consistent hashing.
    """
    def __init__(
        self,
        strategy: RoutingStrategy = RoutingStrategy.DIRECTORY_MAP,
    ) -> None:
        self.strategy = strategy
        self._cells: Dict[str, Cell] = {}
        self._directory: Dict[str, str] = {}  # tenant_id -> cell_id
        self._migrating_tenants: Dict[str, str] = {}  # tenant_id -> target_cell_id
        self._lock = threading.RLock()

    def register_cell(self, cell: Cell) -> None:
        with self._lock:
            self._cells[cell.cell_id] = cell

    def get_cell(self, cell_id: str) -> Optional[Cell]:
        with self._lock:
            return self._cells.get(cell_id)

    def set_tenant_mapping(self, tenant_id: str, cell_id: str) -> None:
        with self._lock:
            self._directory[tenant_id] = cell_id

    def set_migration_route(self, tenant_id: str, target_cell_id: str) -> None:
        with self._lock:
            self._migrating_tenants[tenant_id] = target_cell_id

    def clear_migration_route(self, tenant_id: str) -> None:
        with self._lock:
            self._migrating_tenants.pop(tenant_id, None)

    def resolve_cell_id(self, tenant_id: str) -> str:
        """Determines the target cell ID for a given tenant."""
        with self._lock:
            # 1. Check active migration override first
            if tenant_id in self._migrating_tenants:
                return self._migrating_tenants[tenant_id]

            # 2. Directory Map Strategy
            if self.strategy == RoutingStrategy.DIRECTORY_MAP:
                if tenant_id in self._directory:
                    return self._directory[tenant_id]
                raise KeyError(f"No cell mapping registered for tenant '{tenant_id}'")

            # 3. Deterministic Hashing Strategy
            available_cells = sorted(self._cells.keys())
            if not available_cells:
                raise RuntimeError("No cells registered in router")

            digest = hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()
            index = int(digest[:8], 16) % len(available_cells)
            return available_cells[index]

    def route(self, request: CellRequest) -> CellResponse:
        """Route an incoming request to the tenant's isolated cell."""
        try:
            target_cell_id = self.resolve_cell_id(request.tenant_id)
        except Exception as e:
            return CellResponse(
                request_id=request.request_id,
                cell_id="ROUTER",
                tenant_id=request.tenant_id,
                success=False,
                error=f"Routing failure: {str(e)}",
            )

        with self._lock:
            cell = self._cells.get(target_cell_id)

        if not cell:
            return CellResponse(
                request_id=request.request_id,
                cell_id=target_cell_id,
                tenant_id=request.tenant_id,
                success=False,
                error=f"Target cell '{target_cell_id}' not found",
            )

        return cell.process_request(request)


class CellControlPlane:
    """
    Administrative orchestrator for the cellular fleet.
    Handles cell provisioning, capacity-aware tenant placement,
    noisy-neighbor isolation (VIP cells), and atomic live migrations.
    """
    def __init__(self, router: CellRouter) -> None:
        self.router = router
        self._tenants: Dict[str, Tenant] = {}
        self._lock = threading.RLock()

    def provision_cell(self, cell_id: str, max_tenants: int = 50) -> Cell:
        with self._lock:
            cell = Cell(cell_id=cell_id, max_tenants=max_tenants)
            self.router.register_cell(cell)
            return cell

    def register_tenant(
        self,
        tenant_id: str,
        name: str,
        tier: TenantTier = TenantTier.STANDARD,
        preferred_cell_id: Optional[str] = None,
    ) -> Tenant:
        with self._lock:
            tenant = Tenant(tenant_id=tenant_id, name=name, tier=tier)

            # Cell Placement: Pick preferred or cell with lowest tenant count
            target_cell = None
            if preferred_cell_id:
                target_cell = self.router.get_cell(preferred_cell_id)

            if not target_cell:
                # Find healthy cell with available capacity and lowest occupancy
                healthy_cells = [
                    c for c in self.router._cells.values()
                    if c.state == CellState.HEALTHY and c.tenant_count < c.max_tenants
                ]
                if not healthy_cells:
                    raise RuntimeError("All cells are at full capacity or offline")
                # Sort by lowest occupancy
                healthy_cells.sort(key=lambda c: c.tenant_count)
                target_cell = healthy_cells[0]

            target_cell.assign_tenant(tenant_id)
            tenant.assigned_cell_id = target_cell.cell_id
            self.router.set_tenant_mapping(tenant_id, target_cell.cell_id)
            self._tenants[tenant_id] = tenant
            return tenant

    def isolate_to_vip_cell(self, tenant_id: str, vip_cell_id: str) -> None:
        """
        Noisy neighbor isolation: Move an Enterprise/heavy tenant into a dedicated VIP cell.
        """
        with self._lock:
            tenant = self._tenants.get(tenant_id)
            if not tenant:
                raise KeyError(f"Tenant '{tenant_id}' not registered")

            vip_cell = self.router.get_cell(vip_cell_id)
            if not vip_cell:
                vip_cell = self.provision_cell(vip_cell_id, max_tenants=1)

            self.migrate_tenant(tenant_id, vip_cell_id)

    def migrate_tenant(self, tenant_id: str, target_cell_id: str) -> bool:
        """
        Atomic tenant migration from source cell to target cell.
        Workflow:
        1. Mark tenant as MIGRATING.
        2. Snapshot and copy tenant data from source cell -> target cell.
        3. Atomically switch router directory to target cell.
        4. Remove tenant data from source cell.
        5. Mark tenant as ACTIVE.
        """
        with self._lock:
            tenant = self._tenants.get(tenant_id)
            if not tenant:
                return False

            source_cell_id = tenant.assigned_cell_id
            if source_cell_id == target_cell_id:
                return True

            source_cell = self.router.get_cell(source_cell_id) if source_cell_id else None
            target_cell = self.router.get_cell(target_cell_id)
            if not target_cell:
                return False

            # Step 1: Set migration state
            tenant.is_migrating = True
            tenant.migration_target_cell_id = target_cell_id

            # Step 2: Extract data from source cell
            existing_data = {}
            if source_cell:
                existing_data = source_cell.remove_tenant(tenant_id) or {}

            # Step 3: Populate target cell
            target_cell.import_tenant_data(tenant_id, existing_data)

            # Step 4: Atomically update routing directory
            self.router.set_tenant_mapping(tenant_id, target_cell_id)

            # Step 5: Finalize
            tenant.assigned_cell_id = target_cell_id
            tenant.is_migrating = False
            tenant.migration_target_cell_id = None
            return True

    def calculate_blast_radius(self, failed_cell_ids: List[str]) -> float:
        """
        Calculates the exact percentage of global tenants impacted by a given cell outage.
        Formula: (Tenants in Failed Cells / Total Fleet Tenants) * 100
        """
        with self._lock:
            total_tenants = len(self._tenants)
            if total_tenants == 0:
                return 0.0

            impacted_tenants = sum(
                1 for t in self._tenants.values()
                if t.assigned_cell_id in failed_cell_ids
            )
            return (impacted_tenants / total_tenants) * 100.0
