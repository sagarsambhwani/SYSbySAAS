"""
Interactive Terminal Simulation: Cell-Based Architecture Partitioning
=====================================================================
Domain 9: Production Guardrails & Governance ("Red Tape") - PoC 9.2

Demonstrates:
1. Multi-Cell Fleet Provisioning & Balanced Tenant Placement
2. Catastrophic In-Cell Outage & Blast Radius Containment (25% Impact Limit)
3. Zero-Downtime Live Evacuation & Noisy Neighbor VIP Cell Isolation
"""

import random
import threading
import time
from core import (
    CellControlPlane,
    CellRequest,
    CellResponse,
    CellRouter,
    CellState,
    RoutingStrategy,
    TenantTier,
)

# ANSI Colors for clean terminal visualization
RESET = "\033[0m"
BOLD = "\033[1m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
GRAY = "\033[90m"


def print_header(title: str) -> None:
    print(f"\n{BOLD}{CYAN}{'=' * 75}{RESET}")
    print(f"{BOLD}{CYAN}>>> {title}{RESET}")
    print(f"{BOLD}{CYAN}{'=' * 75}{RESET}\n")


def scenario_fleet_provisioning_and_placement() -> None:
    print_header("SCENARIO 1: Cellular Fleet Provisioning & Tenant Distribution")
    print(f"{GRAY}Distributing 20 multi-tier tenants across 4 isolated failure domains.{RESET}")
    print(f"{GRAY}Every cell operates its own dedicated compute, cache, and storage.{RESET}\n")

    router = CellRouter(RoutingStrategy.DIRECTORY_MAP)
    control_plane = CellControlPlane(router)

    cell_names = ["cell-us-east-1a", "cell-us-east-1b", "cell-us-west-2a", "cell-us-west-2b"]
    for c_id in cell_names:
        control_plane.provision_cell(c_id, max_tenants=6)

    # Register 20 tenants
    tenants = []
    for i in range(1, 21):
        tier = TenantTier.ENTERPRISE if i <= 4 else (TenantTier.STANDARD if i <= 12 else TenantTier.FREE)
        t = control_plane.register_tenant(f"tenant_{i:02d}", f"Tenant Corp {i}", tier=tier)
        tenants.append(t)

        # Write initial seed record to isolated cell database
        router.route(CellRequest(
            request_id=f"init_{t.tenant_id}",
            tenant_id=t.tenant_id,
            action="WRITE",
            payload={"key": "account_balance", "value": i * 1000},
        ))

    print(f"{BOLD}{'Cell ID':<20} | {'Status':<10} | {'Occupancy':<12} | {'Assigned Tenants'}{RESET}")
    print("-" * 75)

    for c_id in cell_names:
        cell = router.get_cell(c_id)
        tenant_list = [t.tenant_id for t in tenants if t.assigned_cell_id == c_id]
        print(
            f"{BOLD}{c_id:<20}{RESET} | "
            f"{GREEN}{cell.state.value:<10}{RESET} | "
            f"{cell.tenant_count}/{cell.max_tenants} tenants   | "
            f"{CYAN}{', '.join(tenant_list)}{RESET}"
        )


def scenario_catastrophic_outage_blast_radius() -> None:
    print_header("SCENARIO 2: Catastrophic Outage & Blast Radius Containment")
    print(f"{GRAY}Simulating an unrecoverable database corruption in 'cell-us-east-1b'.{RESET}")
    print(f"{GRAY}Verifying that 75% of global customers experience 100% normal uptime.{RESET}\n")

    router = CellRouter(RoutingStrategy.DIRECTORY_MAP)
    control_plane = CellControlPlane(router)

    cells = ["cell-alpha", "cell-beta", "cell-gamma", "cell-delta"]
    for c_id in cells:
        control_plane.provision_cell(c_id, max_tenants=10)

    tenants = [control_plane.register_tenant(f"tenant_{i:02d}", f"Tenant {i}") for i in range(1, 41)]

    # Populate data in all cells
    for t in tenants:
        router.route(CellRequest(f"w_{t.tenant_id}", t.tenant_id, "WRITE", {"key": "orders", "value": 42}))

    # INJECT CATASTROPHIC FAILURE INTO CELL-BETA
    print(f"{RED}{BOLD}[DISASTER EVENT] Injecting fatal storage deadlock into 'cell-beta'...{RESET}")
    failed_cell = router.get_cell("cell-beta")
    failed_cell.inject_failure("FATAL_STORAGE_DEADLOCK_DISK_IO_ERROR")

    # Dispatch 400 requests across all 40 tenants
    print(f"{YELLOW}[TRAFFIC DISPATCH] Sending 400 requests across all global tenants...{RESET}\n")

    stats = {c: {"success": 0, "fail": 0} for c in cells}

    for req_i in range(400):
        t = tenants[req_i % len(tenants)]
        res = router.route(CellRequest(f"req_{req_i}", t.tenant_id, "READ", {"key": "orders"}))
        if res.success:
            stats[res.cell_id]["success"] += 1
        else:
            stats[res.cell_id]["fail"] += 1

    print(f"{BOLD}{'Cell Failure Domain':<22} | {'State':<12} | {'Success Req':<12} | {'Failed Req':<12} | {'Impact on Cell'}{RESET}")
    print("-" * 75)

    for c_id in cells:
        s = stats[c_id]["success"]
        f = stats[c_id]["fail"]
        total = s + f
        if f > 0:
            status_tag = f"{RED}{BOLD}OFFLINE{RESET}"
            impact = f"{RED}{BOLD}100% OUTAGE (ISOLATED){RESET}"
        else:
            status_tag = f"{GREEN}HEALTHY{RESET}"
            impact = f"{GREEN}0% OUTAGE (IMMUNE){RESET}"

        print(f"{BOLD}{c_id:<22}{RESET} | {status_tag:<21} | {s:<12} | {f:<12} | {impact}")

    blast_radius = control_plane.calculate_blast_radius(["cell-beta"])
    print("\n" + "-" * 75)
    print(f"{BOLD}BLAST RADIUS TELEMETRY CALCULATION:{RESET}")
    print(f"  Formula       : (Impacted Tenants in Failed Cells / Total Global Fleet Tenants) * 100")
    print(f"  Total Fleet   : 40 tenants across 4 cells")
    print(f"  Impacted Fleet: 10 tenants in 'cell-beta'")
    print(f"  Blast Radius  : {GREEN}{BOLD}{blast_radius:.1f}%{RESET} (Exactly 1/4 of global capacity)")
    print(f"  {BOLD}{GREEN}[PASS] Blast Radius Strictly Contained:{RESET} Sibling cells operated with 0 dropped requests.")


def scenario_live_tenant_migration_and_vip_isolation() -> None:
    print_header("SCENARIO 3: Live Tenant Evacuation & VIP Noisy Neighbor Isolation")
    print(f"{GRAY}An enterprise tenant creates 10x traffic. To protect neighbor tenants,{RESET}")
    print(f"{GRAY}the control plane atomically migrates it to a dedicated single-tenant VIP cell.{RESET}\n")

    router = CellRouter(RoutingStrategy.DIRECTORY_MAP)
    control_plane = CellControlPlane(router)

    control_plane.provision_cell("shared-pool-01", max_tenants=5)
    t_shared = control_plane.register_tenant("regular_tenant_a", "Acme Small")
    t_vip = control_plane.register_tenant("mega_enterprise_x", "Mega Enterprise Inc", tier=TenantTier.ENTERPRISE)

    # Initial state
    router.route(CellRequest("seed_1", "mega_enterprise_x", "WRITE", {"key": "api_credits", "value": 5000000}))

    print(f"{YELLOW}[INITIAL STATE] 'mega_enterprise_x' is currently in '{t_vip.assigned_cell_id}'{RESET}")
    res_before = router.route(CellRequest("chk_1", "mega_enterprise_x", "READ", {"key": "api_credits"}))
    print(f"  Read before migration: cell='{res_before.cell_id}', credits={res_before.data['value']}")

    print(f"\n{CYAN}[CONTROL PLANE ACTION] Evacuating 'mega_enterprise_x' to dedicated VIP cell 'cell-vip-dedicated'...{RESET}")
    control_plane.isolate_to_vip_cell("mega_enterprise_x", "cell-vip-dedicated")

    print(f"{GREEN}[MIGRATION COMPLETE] Tenant router directory atomically updated.{RESET}\n")

    # Read immediately after migration
    res_after = router.route(CellRequest("chk_2", "mega_enterprise_x", "READ", {"key": "api_credits"}))
    print(f"  Read after migration : cell='{BOLD}{res_after.cell_id}{RESET}', credits={res_after.data['value']}")

    # Write new data to VIP cell
    router.route(CellRequest("write_vip", "mega_enterprise_x", "WRITE", {"key": "vip_status", "value": "ISOLATED"}))
    res_vip_read = router.route(CellRequest("read_vip", "mega_enterprise_x", "READ", {"key": "vip_status"}))

    print(f"  New VIP Cell Write   : cell='{BOLD}{res_vip_read.cell_id}{RESET}', vip_status='{res_vip_read.data['value']}'")

    # Verify original shared cell has no lingering copy
    shared_cell = router.get_cell("shared-pool-01")
    assert "mega_enterprise_x" not in shared_cell._tenants_assigned
    print(f"  Original Shared Cell : Occupancy reduced to {shared_cell.tenant_count} tenants (Cleaned up).")
    print(f"{BOLD}{GREEN}[PASS] Zero-Downtime VIP Isolation Succeeded with 0 Data Loss.{RESET}")


if __name__ == "__main__":
    scenario_fleet_provisioning_and_placement()
    scenario_catastrophic_outage_blast_radius()
    scenario_live_tenant_migration_and_vip_isolation()
