"""Multi-Phase Circuit Breaker Simulation & Resilience Telemetry.

Demonstrates an AI Application protected by a Circuit Breaker during:
1. Normal operation with primary model (GPT-4o).
2. Sudden downstream outage / latency storm.
3. Fail-fast protection & seamless fallback routing (Claude 3.5 Sonnet).
4. Cooldown expiration, Canary probing (HALF_OPEN), and self-healing.
"""

import random
import time
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from core import (
    CircuitBreaker,
    CircuitState,
    CircuitBreakerOpenException,
)

console = Console()


class DownstreamLLMService:
    """Simulates a remote LLM API that can be toggled between healthy and degraded."""
    def __init__(self):
        self.is_failing = False
        self.simulated_latency = 0.02  # 20ms baseline

    def generate(self, prompt: str) -> str:
        time.sleep(self.simulated_latency)
        if self.is_failing:
            # Simulate upstream 503 Overloaded or network timeout
            raise ConnectionError("503 Service Unavailable: Primary LLM Gateway Overloaded")
        return f"[GPT-4o Response to '{prompt}']"


def fallback_llm_model(prompt: str) -> str:
    """Instant fallback model (e.g. secondary provider or local lightweight LLM)."""
    return f"[Claude 3.5 Sonnet (Fallback) Response to '{prompt}']"


def run_simulation_phase(
    phase_title: str,
    circuit_breaker: CircuitBreaker,
    downstream: DownstreamLLMService,
    num_requests: int,
    prompt: str,
    make_failing: bool,
    sleep_interval: float = 0.05,
):
    downstream.is_failing = make_failing

    console.print(f"\n[bold yellow]>>> Starting Phase: {phase_title}[/bold yellow]")

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Req #", justify="right", width=6)
    table.add_column("State Before", justify="center", width=12)
    table.add_column("Latency", justify="right", width=10)
    table.add_column("Result / Provider", justify="left", width=42)
    table.add_column("State After", justify="center", width=12)

    for i in range(1, num_requests + 1):
        state_before = circuit_breaker.state.value
        t0 = time.monotonic()
        try:
            res = circuit_breaker.call(
                downstream.generate,
                prompt,
                fallback=fallback_llm_model,
            )
            latency_ms = (time.monotonic() - t0) * 1000
            provider_style = "green" if "GPT-4o" in res else "magenta"
            res_display = f"[{provider_style}]{res}[/{provider_style}]"
        except CircuitBreakerOpenException:
            latency_ms = (time.monotonic() - t0) * 1000
            res_display = "[red]BLOCKED (Fast Fail)[/red]"

        state_after = circuit_breaker.state.value

        state_style_before = "green" if state_before == "CLOSED" else ("red" if state_before == "OPEN" else "yellow")
        state_style_after = "green" if state_after == "CLOSED" else ("red" if state_after == "OPEN" else "yellow")

        table.add_row(
            str(i),
            f"[{state_style_before}]{state_before}[/{state_style_before}]",
            f"{latency_ms:.1f}ms",
            res_display,
            f"[{state_style_after}]{state_after}[/{state_style_after}]",
        )
        time.sleep(sleep_interval)

    console.print(table)


def main():
    console.print(
        Panel.fit(
            "[bold green]SYSbySAAS: Circuit Breaker & Fallback Simulation[/bold green]\n"
            "[dim]Demonstrating failure containment, sub-millisecond fail-fast, and canary self-healing[/dim]",
            border_style="green",
        )
    )

    downstream = DownstreamLLMService()
    cb = CircuitBreaker(
        failure_rate_threshold=0.5,  # Trip if >= 50% fail
        recovery_timeout=1.5,        # 1.5s cooldown before probing in HALF_OPEN
        min_throughput=4,            # Need at least 4 requests before evaluating
        window_size=8,
        half_open_trials=3,          # 3 consecutive canary successes to heal
        name="llm_gateway_breaker",
    )

    # 1. Phase 1: Healthy normal operations
    run_simulation_phase(
        "1. Healthy Operations (Primary Model Available)",
        circuit_breaker=cb,
        downstream=downstream,
        num_requests=6,
        prompt="Explain Quantum Computing",
        make_failing=False,
    )

    # 2. Phase 2: Primary LLM experiences outage storm
    run_simulation_phase(
        "2. Downstream Outage Storm (Primary LLM throws 503s)",
        circuit_breaker=cb,
        downstream=downstream,
        num_requests=6,
        prompt="Summarize Document",
        make_failing=True,
    )

    # 3. Phase 3: Fail-Fast in Action (Primary is OPEN, fallback served in <0.2ms)
    run_simulation_phase(
        "3. Fail-Fast & Fallback Routing (Zero wait on dead primary)",
        circuit_breaker=cb,
        downstream=downstream,
        num_requests=5,
        prompt="Translate to Spanish",
        make_failing=True,
        sleep_interval=0.02,
    )

    # 4. Wait for recovery timeout to transition to HALF_OPEN
    console.print(f"\n[bold blue][WAIT] Cooldown period ({cb.recovery_timeout}s) elapsing... Primary service is recovering...[/bold blue]")
    time.sleep(cb.recovery_timeout + 0.1)

    # 5. Phase 4: Canary Probe & Self-Healing
    run_simulation_phase(
        "4. Canary Probing (HALF_OPEN) -> Automatic Healing (CLOSED)",
        circuit_breaker=cb,
        downstream=downstream,
        num_requests=5,
        prompt="Write a Python function",
        make_failing=False,
    )

    # Final Summary Table
    console.print("\n[bold green]================ FINAL CUMULATIVE METRICS ================[/bold green]")
    stats = cb.get_stats()
    summary_table = Table(title="Circuit Breaker Telemetry Summary", show_header=True, header_style="bold magenta")
    summary_table.add_column("Metric", style="bold white")
    summary_table.add_column("Value", justify="right", style="cyan")

    for k, v in stats.items():
        summary_table.add_row(k.replace("_", " ").title(), str(v))

    console.print(summary_table)


if __name__ == "__main__":
    main()
