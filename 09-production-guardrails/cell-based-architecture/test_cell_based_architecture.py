"""
Unit & Concurrency Tests for Cell-Based Architecture Partitioning
==================================================================
Domain 9: Production Guardrails & Governance ("Red Tape") - PoC 9.2
"""

import threading
import time
import pytest
from core import (
    Cell,
    CellCapacityExceededException,
    CellControlPlane,
    CellRequest,
    CellResponse,
    CellRouter,
    CellState,
    RoutingStrategy,
    TenantTier,
)


class TestCellIsolation:
    def test_cell_crud_isolation(self):
        cell_a = Cell("cell-a", max_tenants=10)
        cell_b = Cell("cell-b", max_tenants=10)

        cell_a.assign_tenant("tenant-1")
        cell_b.assign_tenant("tenant-2")

        # Write to tenant-1 in cell-a
        req_write = CellRequest("r1", "tenant-1", "WRITE", {"key": "balance", "value": 500})
        res_write = cell_a.process_request(req_write)
        assert res_write.success is True
        assert res_write.data["value"] == 500

        # Read same key from tenant-2 in cell-b (must be None, completely isolated)
        req_read = CellRequest("r2", "tenant-2", "READ", {"key": "balance"})
        res_read = cell_b.process_request(req_read)
        assert res_read.success is True
        assert res_read.data["value"] is None


class TestBlastRadiusContainment:
    def test_catastrophic_cell_failure_isolation(self):
        """
        The fundamental invariant of Cellular Architecture:
        A fatal failure in Cell-1 must fail ONLY Cell-1 tenants,
        leaving Cell-2, Cell-3, and Cell-4 operating at 100% success.
        """
        router = CellRouter(RoutingStrategy.DIRECTORY_MAP)
        control_plane = CellControlPlane(router)

        # 1. Provision 4 identical cells
        cells = [control_plane.provision_cell(f"cell-0{i}", max_tenants=10) for i in range(1, 5)]

        # 2. Register 40 tenants (10 per cell)
        tenants = []
        for i in range(1, 41):
            t = control_plane.register_tenant(f"tenant_{i:02d}", f"Customer {i}")
            tenants.append(t)

        assert control_plane.calculate_blast_radius(["cell-01"]) == 25.0

        # Pre-populate records in all tenants
        for t in tenants:
            router.route(CellRequest(f"init_{t.tenant_id}", t.tenant_id, "WRITE", {"key": "status", "value": "active"}))

        # 3. Inject catastrophic database corruption into cell-01
        cell_01 = router.get_cell("cell-01")
        cell_01.inject_failure("UNRECOVERABLE_DB_DEADLOCK")

        # 4. Dispatch traffic to all 40 tenants
        results = [router.route(CellRequest(f"req_{t.tenant_id}", t.tenant_id, "READ", {"key": "status"})) for t in tenants]

        # 5. Verify failure isolation
        cell_01_results = [r for r in results if r.cell_id == "cell-01"]
        other_results = [r for r in results if r.cell_id in ["cell-02", "cell-03", "cell-04"]]

        assert len(cell_01_results) == 10
        assert all(r.success is False for r in cell_01_results)
        assert all("UNRECOVERABLE_DB_DEADLOCK" in str(r.error) for r in cell_01_results)

        assert len(other_results) == 30
        assert all(r.success is True for r in other_results)
        assert all(r.data["value"] == "active" for r in other_results)

        # 6. Heal cell-01 and verify recovery
        cell_01.heal()
        recovered_res = router.route(CellRequest("rec_01", "tenant_01", "READ", {"key": "status"}))
        assert recovered_res.success is True


class TestCellCapacityAndPlacement:
    def test_cell_capacity_limit_enforced(self):
        cell = Cell("tiny-cell", max_tenants=2)
        cell.assign_tenant("t1")
        cell.assign_tenant("t2")

        with pytest.raises(CellCapacityExceededException):
            cell.assign_tenant("t3")

    def test_placement_steers_to_underutilized_cells(self):
        router = CellRouter(RoutingStrategy.DIRECTORY_MAP)
        cp = CellControlPlane(router)

        c1 = cp.provision_cell("cell-a", max_tenants=2)
        c2 = cp.provision_cell("cell-b", max_tenants=5)

        t1 = cp.register_tenant("t1", "Customer 1")
        t2 = cp.register_tenant("t2", "Customer 2")
        t3 = cp.register_tenant("t3", "Customer 3")

        # Since cell-a fills up after 2 tenants, t3 must be placed in cell-b
        assigned_cells = {t1.assigned_cell_id, t2.assigned_cell_id, t3.assigned_cell_id}
        assert "cell-a" in assigned_cells
        assert "cell-b" in assigned_cells


class TestNoisyNeighborVIPIsolation:
    def test_isolate_noisy_neighbor_to_vip_cell(self):
        router = CellRouter(RoutingStrategy.DIRECTORY_MAP)
        cp = CellControlPlane(router)

        c1 = cp.provision_cell("shared-cell-01", max_tenants=10)
        tenant_vip = cp.register_tenant("vip_corp", "Mega Corp", tier=TenantTier.ENTERPRISE)
        assert tenant_vip.assigned_cell_id == "shared-cell-01"

        # Write data prior to isolation
        router.route(CellRequest("req_1", "vip_corp", "WRITE", {"key": "orders", "value": 999}))

        # Isolate to dedicated VIP cell
        cp.isolate_to_vip_cell("vip_corp", "vip-cell-dedicated")
        assert tenant_vip.assigned_cell_id == "vip-cell-dedicated"

        # Verify data persisted across migration to VIP cell
        res = router.route(CellRequest("req_2", "vip_corp", "READ", {"key": "orders"}))
        assert res.success is True
        assert res.cell_id == "vip-cell-dedicated"
        assert res.data["value"] == 999


class TestAtomicTenantMigration:
    def test_live_migration_preserves_data(self):
        router = CellRouter(RoutingStrategy.DIRECTORY_MAP)
        cp = CellControlPlane(router)

        cp.provision_cell("source-cell", max_tenants=5)
        cp.provision_cell("dest-cell", max_tenants=5)

        cp.register_tenant("migrating_user", "User Move")
        router.route(CellRequest("w1", "migrating_user", "WRITE", {"key": "profile", "value": {"name": "Alice"}}))

        # Perform migration
        success = cp.migrate_tenant("migrating_user", "dest-cell")
        assert success is True

        # Verify subsequent read routes to dest-cell with intact payload
        res = router.route(CellRequest("r1", "migrating_user", "READ", {"key": "profile"}))
        assert res.success is True
        assert res.cell_id == "dest-cell"
        assert res.data["value"] == {"name": "Alice"}


class TestRoutingStrategies:
    def test_consistent_hash_routing(self):
        router = CellRouter(RoutingStrategy.HASH_RING)
        c1 = Cell("cell-west-1")
        c2 = Cell("cell-west-2")
        router.register_cell(c1)
        router.register_cell(c2)

        # Deterministic hashing: same tenant always resolves to the same cell
        cell_id_1 = router.resolve_cell_id("tenant_apple")
        for _ in range(10):
            assert router.resolve_cell_id("tenant_apple") == cell_id_1


class TestConcurrentMultiCellTraffic:
    def test_high_concurrency_across_fleet(self):
        router = CellRouter(RoutingStrategy.DIRECTORY_MAP)
        cp = CellControlPlane(router)

        for i in range(1, 5):
            cp.provision_cell(f"fleet-cell-0{i}", max_tenants=20)

        for i in range(1, 21):
            cp.register_tenant(f"user_{i}", f"User {i}")

        errors = []

        def worker(worker_id: int):
            for j in range(25):
                t_id = f"user_{(worker_id % 20) + 1}"
                res = router.route(CellRequest(f"req_{worker_id}_{j}", t_id, "WRITE", {"key": "counter", "value": j}))
                if not res.success:
                    errors.append(res.error)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
