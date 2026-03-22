"""
TheNightOps CLI — Command-line interface for managing and running the agent.

Usage:
    nightops init                — Interactive setup wizard
    nightops auto-config         — Auto-discover environment and configure
    nightops verify              — Verify all dependencies and configuration
    nightops dashboard           — Open the real-time investigation dashboard
    nightops mcp start --all     — Start all MCP servers
    nightops demo deploy         — Deploy demo app to GKE
    nightops demo trigger        — Trigger a demo incident scenario
    nightops agent run           — Start the agent and investigate
    nightops agent watch         — Start autonomous mode (webhooks + event watcher)
    nightops metrics             — Show impact metrics
    nightops policies            — Show remediation policies
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from nightops.core.config import NightOpsConfig, SUPPORTED_MODELS
from nightops.core.logging import console as rich_console, print_banner

app = typer.Typer(
    name="nightops",
    help="TheNightOps — Autonomous SRE Agent CLI",
    no_args_is_help=True,
)
mcp_app = typer.Typer(help="Manage MCP servers")
demo_app = typer.Typer(help="Manage demo scenarios")
agent_app = typer.Typer(help="Run the investigation agent")

app.add_typer(mcp_app, name="mcp")
app.add_typer(demo_app, name="demo")
app.add_typer(agent_app, name="agent")


# ── Init Command ──────────────────────────────────────────────────


@app.command()
def init() -> None:
    """Run the interactive setup wizard to configure TheNightOps."""
    from nightops.init_wizard import run_wizard

    run_wizard()


# ── Auto-Config Command ──────────────────────────────────────────


@app.command("auto-config")
def auto_config(
    save: bool = typer.Option(True, "--save/--dry-run", help="Save discovered config"),
) -> None:
    """Auto-discover environment and generate configuration.

    Probes for GCP project, GKE clusters, Grafana instance, and
    notification channels, then generates a config automatically.
    """
    print_banner()
    rich_console.print(
        Panel(
            "Auto-discovering your environment...",
            title="TheNightOps Auto-Config",
            style="cyan",
        )
    )

    async def _discover():
        from nightops.core.autodiscovery import EnvironmentDiscovery, generate_config_from_discovery
        import yaml

        discovery = EnvironmentDiscovery()
        env = await discovery.discover()

        table = Table(title="Discovered Environment")
        table.add_column("Component", style="cyan")
        table.add_column("Status", style="bold")
        table.add_column("Details", style="dim")

        if env.gcp.project_id:
            table.add_row("GCP Project", "[green]✓ Found[/green]", env.gcp.project_id)
            for api in env.gcp.apis_enabled:
                table.add_row(f"  API: {api}", "[green]✓ Enabled[/green]", "")
        else:
            table.add_row("GCP Project", "[red]✗ Not found[/red]", "Set GCP_PROJECT_ID or run gcloud auth")

        if env.clusters:
            for c in env.clusters:
                ctx = " (current)" if c.is_current_context else ""
                table.add_row(
                    f"GKE Cluster: {c.name}",
                    f"[green]✓ {c.status}{ctx}[/green]",
                    f"{c.location} ({c.project_id})",
                )
        else:
            table.add_row("GKE Clusters", "[yellow]⚠ None found[/yellow]", "")

        if env.grafana.reachable:
            table.add_row("Grafana", "[green]✓ Reachable[/green]", env.grafana.url)
        else:
            table.add_row("Grafana", "[yellow]⚠ Not reachable[/yellow]", env.grafana.url or "Not configured")

        for name, available in [
            ("Slack", env.notifications.slack),
            ("Email", env.notifications.email),
            ("Telegram", env.notifications.telegram),
            ("WhatsApp", env.notifications.whatsapp),
        ]:
            status = "[green]✓ Configured[/green]" if available else "[dim]— Not configured[/dim]"
            table.add_row(f"Notification: {name}", status, "")

        if env.in_cluster:
            table.add_row("Kubernetes", "[green]✓ In-cluster[/green]", "")
        elif env.kubeconfig_available:
            table.add_row("Kubernetes", "[green]✓ Kubeconfig[/green]", "")
        else:
            table.add_row("Kubernetes", "[yellow]⚠ No access[/yellow]", "")

        rich_console.print(table)

        config = generate_config_from_discovery(env)

        if save:
            config_path = Path("config/nightops-auto.yaml")
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(yaml.dump(config, default_flow_style=False))
            rich_console.print(f"\n[green]✓ Auto-generated config saved to {config_path}[/green]")
            rich_console.print("[dim]Review and rename to config/nightops.yaml to use.[/dim]")
        else:
            rich_console.print("\n[bold]Generated Configuration:[/bold]")
            rich_console.print(yaml.dump(config, default_flow_style=False))

    asyncio.run(_discover())


# ── Dashboard Command ─────────────────────────────────────────────


@app.command()
def dashboard(
    port: int = typer.Option(8888, "--port", "-p", help="Dashboard port"),
    host: str = typer.Option("0.0.0.0", "--host", help="Dashboard host"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open browser automatically"),
) -> None:
    """Launch the real-time investigation dashboard."""
    import webbrowser

    from nightops.dashboard.app import create_app

    print_banner()
    rich_console.print(
        Panel(
            f"Starting Investigation Dashboard\n"
            f"URL: [bold cyan]http://localhost:{port}[/bold cyan]",
            title="TheNightOps Dashboard",
            style="cyan",
        )
    )

    if open_browser:
        webbrowser.open(f"http://localhost:{port}")

    dashboard_app = create_app()

    import uvicorn
    uvicorn.run(dashboard_app, host=host, port=port, log_level="info")


# ── Metrics Command ──────────────────────────────────────────────


@app.command()
def metrics(
    period: int = typer.Option(30, "--period", "-p", help="Period in days"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c"),
) -> None:
    """Show impact metrics — MTTR, incidents handled, hours saved."""
    print_banner()
    config = _load_config(config_path)

    from nightops.metrics.tracker import MetricsTracker

    tracker = MetricsTracker(config.metrics)
    summary = tracker.get_impact_summary(period_days=period)

    table = Table(title=f"TheNightOps Impact — Last {period} Days")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="bold")

    table.add_row("Total Incidents", str(summary.total_incidents))
    table.add_row("Avg MTTR", f"{summary.avg_mttr_seconds:.0f}s ({summary.avg_mttr_seconds / 60:.1f} min)")
    table.add_row("MTTR Trend", f"{summary.mttr_trend_percent:+.1f}%")
    table.add_row("Auto-Resolved", str(summary.incidents_auto_resolved))
    table.add_row("Required Human", str(summary.incidents_requiring_human))
    table.add_row("Accuracy Rate", f"{summary.accuracy_rate:.0%}")
    table.add_row("Hours Saved", f"{summary.estimated_hours_saved:.1f}h")

    rich_console.print(table)

    if summary.total_incidents == 0:
        rich_console.print(
            "\n[dim]No investigations recorded yet. Run some investigations to see impact metrics.[/dim]"
        )


# ── Policies Command ─────────────────────────────────────────────


@app.command()
def policies(
    config_path: Optional[str] = typer.Option(None, "--config", "-c"),
) -> None:
    """Show remediation policies — what's auto-approved vs requires human approval."""
    print_banner()
    config = _load_config(config_path)

    from nightops.remediation.policy_engine import PolicyEngine

    engine = PolicyEngine(config.remediation.policy_path)
    summary = engine.get_policy_summary()

    table = Table(title="Remediation Policies")
    table.add_column("Action", style="cyan")
    table.add_column("Policy", style="bold")

    for action, policy_status in summary.items():
        if "BLOCKED" in policy_status:
            style = "[red]"
        elif "AUTO-APPROVE" in policy_status:
            style = "[green]"
        elif "AUTO in" in policy_status:
            style = "[yellow]"
        else:
            style = "[dim]"
        table.add_row(action, f"{style}{policy_status}[/]")

    rich_console.print(table)


def _load_config(config_path: Optional[str] = None) -> NightOpsConfig:
    """Load configuration."""
    return NightOpsConfig.load(config_path)


# ── Top-level Commands ─────────────────────────────────────────────


@app.command()
def verify(
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Config file path"),
) -> None:
    """Verify all dependencies and configuration are set up correctly."""
    print_banner()

    checks = []

    py_version = sys.version_info
    checks.append(
        ("Python 3.11+", py_version >= (3, 11), f"{py_version.major}.{py_version.minor}")
    )

    try:
        result = subprocess.run(["gcloud", "--version"], capture_output=True, text=True)
        checks.append(("Google Cloud SDK", result.returncode == 0, "installed"))
    except FileNotFoundError:
        checks.append(("Google Cloud SDK", False, "not found"))

    try:
        result = subprocess.run(["kubectl", "version", "--client"], capture_output=True, text=True)
        checks.append(("kubectl", result.returncode == 0, "installed"))
    except FileNotFoundError:
        checks.append(("kubectl", False, "not found"))

    try:
        config = _load_config(config_path)
        checks.append(("Configuration", True, "loaded"))
        checks.append(("GCP Project ID", bool(config.cloud_observability.project_id), config.cloud_observability.project_id or "not set"))
        checks.append(("Cloud Observability MCP", config.cloud_observability.enabled, f"official ({config.cloud_observability.endpoint})"))
        checks.append(("GKE MCP", config.gke.enabled, f"official (cluster: {config.gke.cluster or 'not set'})"))
        if config.grafana.enabled:
            checks.append(("Grafana MCP", True, f"official ({config.grafana.url})"))
            checks.append(("Grafana Token", bool(config.grafana.service_account_token), "configured" if config.grafana.service_account_token else "not set"))
        else:
            checks.append(("Grafana MCP", True, "disabled"))

        if config.slack.enabled:
            checks.append(("Slack Bot Token", bool(config.slack.bot_token), "configured" if config.slack.bot_token else "not set"))
        else:
            checks.append(("Slack Bot Token", True, "disabled"))
        checks.append(("Model", True, config.agent.model))

        # New capabilities
        checks.append(("Webhook Ingestion", config.webhook.enabled, f"port {config.webhook.port}"))
        checks.append(("Event Watcher", config.event_watcher.enabled, f"namespaces: {config.event_watcher.namespaces}"))
        checks.append(("Incident Memory", config.intelligence.enabled, config.intelligence.store_path))
        checks.append(("Remediation Engine", config.remediation.enabled, "graduated autonomy"))
        checks.append(("Proactive Checks", config.proactive.enabled, f"every {config.proactive.check_interval_seconds}s"))
        checks.append(("Impact Metrics", config.metrics.enabled, config.metrics.store_path))

        if config.clusters:
            checks.append(("Multi-Cluster", True, f"{len(config.clusters)} cluster(s)"))
    except Exception as e:
        checks.append(("Configuration", False, str(e)))

    packages = ["google.adk", "mcp", "httpx", "pydantic"]
    for pkg in packages:
        try:
            __import__(pkg)
            checks.append((f"Package: {pkg}", True, "installed"))
        except ImportError:
            checks.append((f"Package: {pkg}", False, "not installed"))

    table = Table(title="TheNightOps — Dependency Check")
    table.add_column("Component", style="cyan")
    table.add_column("Status", style="bold")
    table.add_column("Details", style="dim")

    all_passed = True
    for name, ok, detail in checks:
        status = "[green]✓ PASS[/green]" if ok else "[red]✗ FAIL[/red]"
        if not ok:
            all_passed = False
        table.add_row(name, status, str(detail))

    rich_console.print(table)

    if all_passed:
        rich_console.print("\n[bold green]All checks passed! TheNightOps is ready.[/bold green]")
    else:
        rich_console.print(
            "\n[bold yellow]Some checks failed. Fix the issues above before running.[/bold yellow]"
        )


# ── MCP Server Commands ────────────────────────────────────────────


@mcp_app.command("start")
def mcp_start(
    all_servers: bool = typer.Option(False, "--all", help="Start all MCP servers"),
    server_name: Optional[str] = typer.Argument(None, help="Specific server to start"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c"),
) -> None:
    """Start MCP servers."""
    config = _load_config(config_path)

    custom_servers = {
        "kubernetes": ("nightops.mcp_servers.kubernetes.server", config.kubernetes.port),
        "cloud_logging": ("nightops.mcp_servers.cloud_logging.server", config.cloud_logging_custom.port),
        "slack": ("nightops.mcp_servers.slack.server", config.slack.port),
        "notifications": ("nightops.mcp_servers.notifications.server", config.notifications.port),
    }

    if all_servers:
        rich_console.print(
            "[dim]Note: Official MCP servers don't need local processes:\n"
            "  • Cloud Observability & GKE — managed remotely by Google\n"
            "  • Grafana — runs as stdio subprocess via 'uvx mcp-grafana'\n"
            "Starting custom SSE servers only.[/dim]\n"
        )

    servers = custom_servers
    to_start = servers if all_servers else {server_name: servers.get(server_name)} if server_name else {}

    if not to_start:
        rich_console.print("[red]Specify --all or a server name (kubernetes, cloud_logging, slack, notifications)[/red]")
        raise typer.Exit(1)

    processes = []
    for name, (module, port) in to_start.items():
        if module is None:
            rich_console.print(f"[red]Unknown server: {name}[/red]")
            continue

        rich_console.print(f"[cyan]Starting {name} MCP server on port {port}...[/cyan]")
        proc = subprocess.Popen(
            [sys.executable, "-m", module],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        processes.append((name, proc, port))
        rich_console.print(f"[green]  ✓ {name} started (PID: {proc.pid}, port: {port})[/green]")

    if processes:
        rich_console.print(f"\n[bold green]{len(processes)} MCP server(s) running. Press Ctrl+C to stop.[/bold green]")
        try:
            for _, proc, _ in processes:
                proc.wait()
        except KeyboardInterrupt:
            rich_console.print("\n[yellow]Stopping MCP servers...[/yellow]")
            for name, proc, _ in processes:
                proc.terminate()
                rich_console.print(f"  Stopped {name}")


@mcp_app.command("status")
def mcp_status() -> None:
    """Check status of running MCP servers."""
    import httpx

    official_servers = {
        "Cloud Observability": ("Google Cloud", "logging.googleapis.com/mcp", "remote"),
        "GKE": ("Google Cloud", "container.googleapis.com/mcp", "remote"),
        "Grafana": ("Grafana Labs", "uvx mcp-grafana", "stdio"),
    }

    custom_servers = {
        "kubernetes": 8002,
        "cloud_logging": 8001,
        "slack": 8004,
        "notifications": 8005,
    }

    table = Table(title="MCP Server Status")
    table.add_column("Server", style="cyan")
    table.add_column("Type")
    table.add_column("Endpoint/Port")
    table.add_column("Status", style="bold")

    for name, (provider, endpoint, transport) in official_servers.items():
        label = f"[blue]official[/blue] ({transport})"
        if transport == "remote":
            table.add_row(name, label, endpoint, f"[green]✓ Managed by {provider}[/green]")
        else:
            try:
                result = subprocess.run(["uvx", "--version"], capture_output=True, text=True)
                table.add_row(name, label, endpoint, "[green]✓ uvx available[/green]")
            except FileNotFoundError:
                table.add_row(name, label, endpoint, "[red]✗ uvx not installed (pip install uv)[/red]")

    for name, port in custom_servers.items():
        try:
            resp = httpx.get(f"http://localhost:{port}/sse", timeout=2)
            table.add_row(name, "[yellow]custom[/yellow]", str(port), "[green]✓ Running[/green]")
        except Exception:
            table.add_row(name, "[yellow]custom[/yellow]", str(port), "[red]✗ Not running[/red]")

    rich_console.print(table)


# ── Demo Commands ──────────────────────────────────────────────────


@demo_app.command("deploy")
def demo_deploy(
    config_path: Optional[str] = typer.Option(None, "--config", "-c"),
) -> None:
    """Deploy the demo application to GKE."""
    config = _load_config(config_path)
    project_id = config.cloud_observability.project_id or config.gke.project_id
    region = os.environ.get("GCP_REGION", "us-central1")

    if not project_id:
        rich_console.print("[red]GCP_PROJECT_ID not set. Configure it in config/.env or nightops.yaml[/red]")
        raise typer.Exit(1)

    demo_image = f"{region}-docker.pkg.dev/{project_id}/nightops/nightops-demo-api:latest"

    rich_console.print(Panel(
        f"Deploying NightOps demo to GKE\nProject: {project_id}\nImage: {demo_image}",
        title="TheNightOps Demo Deployment",
        style="cyan",
    ))

    # Use generated manifests if available, fall back to templates with sed
    project_root = Path(__file__).parent.parent
    generated_demo = project_root / "deploy" / "generated" / "demo" / "demo-app.yaml"
    template_demo = project_root / "demo" / "k8s_manifests" / "demo-app.yaml"

    steps: list[tuple[str, list[str]]] = [
        ("Building demo container", ["docker", "build", "-t", demo_image, "-f", "demo/Dockerfile", "demo/"]),
        ("Pushing to Artifact Registry", ["docker", "push", demo_image]),
    ]

    for step_name, cmd in steps:
        rich_console.print(f"\n[cyan]{step_name}...[/cyan]")
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            rich_console.print(f"[red]  ✗ Failed: {result.stderr}[/red]")
            raise typer.Exit(1)
        rich_console.print(f"[green]  ✓ {step_name} complete[/green]")

    # Apply manifests — prefer generated, fall back to template with sed
    rich_console.print(f"\n[cyan]Applying demo manifests...[/cyan]")
    if generated_demo.exists():
        result = subprocess.run(
            ["kubectl", "apply", "-f", str(generated_demo)],
            capture_output=True, text=True,
        )
    else:
        # Render template on the fly
        with open(template_demo) as f:
            rendered = f.read().replace("DEMO_IMAGE", demo_image)
        result = subprocess.run(
            ["kubectl", "apply", "-f", "-"],
            input=rendered, capture_output=True, text=True,
        )

    if result.returncode != 0:
        rich_console.print(f"[red]  ✗ Failed: {result.stderr}[/red]")
        raise typer.Exit(1)
    rich_console.print(f"[green]  ✓ Demo app deployed[/green]")

    rich_console.print("\n[bold green]Demo application deployed successfully![/bold green]")
    rich_console.print("Run 'nightops demo trigger --scenario memory-leak' to start a scenario.")


@demo_app.command("trigger")
def demo_trigger(
    scenario: str = typer.Option(..., "--scenario", "-s", help="Scenario name"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c"),
) -> None:
    """Trigger a demo incident scenario."""
    config = _load_config(config_path)

    project_root = Path(__file__).parent.parent
    generated_dir = project_root / "deploy" / "generated" / "demo"
    template_dir = project_root / "demo" / "k8s_manifests"

    # Prefer generated manifests (have real image), fall back to templates
    def _get_manifest(name: str) -> Path:
        generated = generated_dir / name
        return generated if generated.exists() else template_dir / name

    scenario_map = {
        "memory-leak": _get_manifest("memory-leak-scenario.yaml"),
        "cpu-spike": _get_manifest("cpu-spike-scenario.yaml"),
    }

    rich_console.print(Panel(
        f"Triggering scenario: [bold]{scenario}[/bold]",
        title="TheNightOps — Incident Simulation",
        style="yellow",
    ))

    if scenario in scenario_map:
        manifest = scenario_map[scenario]
        result = subprocess.run(
            ["kubectl", "apply", "-f", str(manifest)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            rich_console.print(f"[green]✓ Scenario '{scenario}' triggered via manifest[/green]")
        else:
            rich_console.print(f"[red]✗ Failed: {result.stderr}[/red]")
    elif scenario in ("cascading-failure", "config-drift", "oom-kill"):
        env_patches = {
            "cascading-failure": {"FAILURE_MODE": "cascading-failure"},
            "config-drift": {"FAILURE_MODE": "config-drift", "ERROR_RATE_PERCENT": "50"},
            "oom-kill": {"FAILURE_MODE": "oom-kill"},
        }
        env_vars = env_patches[scenario]
        env_args = [f"{k}={v}" for k, v in env_vars.items()]
        result = subprocess.run(
            ["kubectl", "set", "env", "deployment/demo-api", "-n", "nightops-demo", *env_args],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            rich_console.print(f"[green]✓ Scenario '{scenario}' triggered via env patch[/green]")
        else:
            rich_console.print(f"[red]✗ Failed: {result.stderr}[/red]")
    else:
        rich_console.print(f"[red]Unknown scenario: {scenario}[/red]")
        rich_console.print("Available: memory-leak, cpu-spike, cascading-failure, config-drift, oom-kill")
        raise typer.Exit(1)

    rich_console.print("\n[dim]The incident will develop over the next 2-5 minutes.[/dim]")
    rich_console.print("Run 'nightops agent watch' for autonomous detection, or")
    rich_console.print("'nightops agent run --interactive' for manual investigation.")


@demo_app.command("reset")
def demo_reset() -> None:
    """Reset the demo environment to a clean state."""
    rich_console.print("[cyan]Resetting demo environment...[/cyan]")

    commands = [
        ["kubectl", "set", "env", "deployment/demo-api", "-n", "nightops-demo", "FAILURE_MODE=none", "ERROR_RATE_PERCENT=0"],
        ["kubectl", "rollout", "restart", "deployment/demo-api", "-n", "nightops-demo"],
    ]

    for cmd in commands:
        subprocess.run(cmd, capture_output=True)

    rich_console.print("[green]✓ Demo environment reset to clean state[/green]")


# ── Agent Commands ─────────────────────────────────────────────────


@agent_app.command("run")
def agent_run(
    interactive: bool = typer.Option(False, "--interactive", "-i", help="Interactive mode"),
    incident: Optional[str] = typer.Option(None, "--incident", help="Incident description"),
    simple: bool = typer.Option(False, "--simple", "-s", help="Simple mode: single agent with kubectl tools (no MCP)"),
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c"),
) -> None:
    """Run TheNightOps agent to investigate an incident.

    Use --simple for reliable single-agent mode (kubectl tools, no MCP dependency).
    """
    if debug:
        logging.basicConfig(level=logging.DEBUG)

    print_banner()
    config = _load_config(config_path)

    if simple:
        if interactive:
            _run_interactive(config, verbose, simple_mode=True)
        elif incident:
            asyncio.run(_run_simple_single_investigation(config, incident))
        else:
            rich_console.print("[yellow]Specify --interactive or --incident <description>[/yellow]")
            raise typer.Exit(1)
    elif interactive:
        _run_interactive(config, verbose)
    elif incident:
        asyncio.run(_run_single_investigation(config, incident))
    else:
        rich_console.print("[yellow]Specify --interactive or --incident <description>[/yellow]")
        raise typer.Exit(1)


@agent_app.command("watch")
def agent_watch(
    simple: bool = typer.Option(False, "--simple", "-s", help="Simple mode: single agent with kubectl tools (no MCP)"),
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging"),
    config_path: Optional[str] = typer.Option(None, "--config", "-c"),
) -> None:
    """Start autonomous watch mode — webhooks + event watcher + proactive checks.

    Use --simple for reliable single-agent mode (kubectl tools, no MCP dependency).

    This is the fully autonomous mode where TheNightOps:
    1. Listens for webhook alerts from Grafana/Alertmanager/PagerDuty
    2. Watches Kubernetes events for OOMKills, CrashLoops, etc.
    3. Runs proactive health checks on a schedule
    4. Auto-investigates and remediates (per policy)
    """
    if debug:
        logging.basicConfig(level=logging.DEBUG)

    print_banner()
    config = _load_config(config_path)

    mode_label = "[bold yellow]Simple Mode (kubectl tools, no MCP)[/bold yellow]" if simple else "[bold]Autonomous Watch Mode[/bold]"

    rich_console.print(
        Panel(
            f"{mode_label}\n\n"
            f"Webhook receiver:  port {config.webhook.port} {'[green]enabled[/green]' if config.webhook.enabled else '[red]disabled[/red]'}\n"
            f"Event watcher:     {'[green]enabled[/green]' if config.event_watcher.enabled else '[red]disabled[/red]'} ({', '.join(config.event_watcher.namespaces)})\n"
            f"Proactive checks:  {'[green]enabled[/green]' if config.proactive.enabled else '[red]disabled[/red]'} (every {config.proactive.check_interval_seconds}s)\n"
            f"Incident memory:   {'[green]enabled[/green]' if config.intelligence.enabled else '[red]disabled[/red]'}\n"
            f"Remediation:       {'[green]enabled[/green]' if config.remediation.enabled else '[red]disabled[/red]'}\n"
            f"Model:             {config.agent.model}",
            title="TheNightOps — Always On",
            style="cyan",
        )
    )

    asyncio.run(_run_watch_mode(config, simple_mode=simple))


async def _run_watch_mode(config: NightOpsConfig, simple_mode: bool = False) -> None:
    """Run the autonomous watch mode with all subsystems."""
    import os
    import uvicorn

    from nightops.ingestion.deduplication import AlertDeduplicator

    if simple_mode:
        from nightops.agents.simple_agent import run_simple_investigation as _run_inv
    else:
        from nightops.agents.root_orchestrator import run_investigation as _run_inv

    deduplicator = AlertDeduplicator(
        window_seconds=config.webhook.dedup_window_seconds,
    )

    dashboard_url = os.getenv("NIGHTOPS_DASHBOARD_URL", "http://localhost:8888")

    async def on_new_incident(incident):
        rich_console.print(
            f"\n[bold yellow]New incident detected:[/bold yellow] {incident.title}\n"
            f"  Source: {incident.source} | Severity: {incident.severity.value} | ID: {incident.id}"
        )
        try:
            result = await _run_inv(
                config,
                incident.description,
                dashboard_url=dashboard_url,
                incident_id=incident.id,
            )
            result_key = "result" if simple_mode else "investigation_result"
            rich_console.print(
                Panel(
                    result[result_key][:2000],
                    title=f"Investigation Complete — {incident.id}",
                    style="green",
                )
            )
        except Exception as e:
            rich_console.print(f"[red]Investigation failed for {incident.id}: {e}[/red]")
            cause = e.__cause__ if e.__cause__ else e
            if hasattr(cause, "exceptions"):
                for i, sub_exc in enumerate(cause.exceptions, 1):
                    rich_console.print(f"[red]  [{i}] {type(sub_exc).__name__}: {sub_exc}[/red]")
            rich_console.print(
                "[dim]Tip: Run with --debug for full error details. "
                "Check GCP auth: gcloud auth application-default login[/dim]"
            )

    tasks = []

    if config.webhook.enabled:
        from nightops.ingestion.webhook_receiver import create_webhook_app

        webhook_app = create_webhook_app(
            deduplicator=deduplicator,
            on_new_incident=on_new_incident,
        )
        webhook_config = uvicorn.Config(
            webhook_app,
            host=config.webhook.host,
            port=config.webhook.port,
            log_level="warning",
        )
        webhook_server = uvicorn.Server(webhook_config)
        tasks.append(asyncio.create_task(webhook_server.serve()))
        rich_console.print(f"[green]✓ Webhook receiver started on port {config.webhook.port}[/green]")

    if config.event_watcher.enabled:
        from nightops.ingestion.event_watcher import EventWatcher

        watcher = EventWatcher(
            config=config.event_watcher,
            deduplicator=deduplicator,
            on_new_incident=on_new_incident,
        )
        await watcher.start()
        rich_console.print("[green]✓ Kubernetes event watcher started[/green]")

    if config.proactive.enabled:
        from nightops.proactive.scheduler import ProactiveScheduler

        scheduler = ProactiveScheduler(
            config=config.proactive,
            deduplicator=deduplicator,
            on_new_incident=on_new_incident,
        )
        await scheduler.start()
        rich_console.print("[green]✓ Proactive health check scheduler started[/green]")

    rich_console.print("\n[bold green]TheNightOps is watching. Press Ctrl+C to stop.[/bold green]\n")

    try:
        if tasks:
            await asyncio.gather(*tasks)
        else:
            while True:
                await asyncio.sleep(1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        rich_console.print("\n[yellow]Shutting down watch mode...[/yellow]")


def _run_interactive(config: NightOpsConfig, verbose: bool = False, simple_mode: bool = False) -> None:
    """Run the agent in interactive mode."""
    mode_label = "Simple Mode (kubectl)" if simple_mode else "Interactive Mode"
    rich_console.print(
        Panel(
            f"TheNightOps is running in {mode_label.lower()}.\n"
            "Paste an incident description or Grafana alert to begin investigation.\n"
            "Type 'quit' to exit.",
            title=mode_label,
            style="cyan",
        )
    )

    run_fn = _run_simple_single_investigation if simple_mode else _run_single_investigation

    while True:
        try:
            rich_console.print("\n[bold cyan]nightops>[/bold cyan] ", end="")
            user_input = input().strip()

            if user_input.lower() in ("quit", "exit", "q"):
                rich_console.print("[dim]Goodbye![/dim]")
                break

            if not user_input:
                continue

            rich_console.print("\n[bold magenta]Starting investigation...[/bold magenta]\n")
            asyncio.run(run_fn(config, user_input))

        except (KeyboardInterrupt, EOFError):
            rich_console.print("\n[dim]Goodbye![/dim]")
            break


async def _run_simple_single_investigation(
    config: NightOpsConfig, incident_description: str,
) -> None:
    """Run a single investigation using simple mode (kubectl tools, no MCP)."""
    import os
    from nightops.agents.simple_agent import run_simple_investigation

    rich_console.print(
        Panel(
            f"[bold]Incident:[/bold] {incident_description[:200]}\n"
            "[dim]Mode: Simple (kubectl tools, no MCP)[/dim]",
            title="Simple Investigation Started",
            style="yellow",
        )
    )

    dashboard_url = os.getenv("NIGHTOPS_DASHBOARD_URL", "http://localhost:8888")

    try:
        result = await run_simple_investigation(
            config, incident_description, dashboard_url=dashboard_url,
        )

        rich_console.print(
            Panel(
                result["result"],
                title="Investigation Complete",
                style="green",
            )
        )
        rich_console.print(
            f"[dim]Tools called: {result.get('tools_called', 0)} | "
            f"Status: {result.get('status', 'unknown')}[/dim]"
        )

    except Exception as e:
        rich_console.print(f"\n[red]Investigation failed: {e}[/red]")
        rich_console.print("[dim]Tip: Ensure kubectl is configured and can reach your cluster.[/dim]")


async def _run_single_investigation(config: NightOpsConfig, incident_description: str) -> None:
    """Run a single incident investigation."""
    import os
    from nightops.agents.root_orchestrator import run_investigation

    rich_console.print(
        Panel(
            f"[bold]Incident:[/bold] {incident_description[:200]}",
            title="Investigation Started",
            style="yellow",
        )
    )

    dashboard_url = os.getenv("NIGHTOPS_DASHBOARD_URL", "http://localhost:8888")

    try:
        result = await run_investigation(config, incident_description, dashboard_url=dashboard_url)

        rich_console.print(
            Panel(
                result["investigation_result"],
                title="Investigation Complete",
                style="green",
            )
        )

        if result.get("historical_matches", 0) > 0:
            rich_console.print(
                f"[dim]Matched {result['historical_matches']} similar historical incident(s)[/dim]"
            )
        rich_console.print(f"[dim]Tools called: {result.get('tools_called', 0)}[/dim]")

    except Exception as e:
        rich_console.print(f"\n[red]Investigation failed: {e}[/red]")
        # Show sub-exceptions from ExceptionGroup (MCP/TaskGroup errors)
        cause = e.__cause__ if e.__cause__ else e
        if hasattr(cause, "exceptions"):
            for i, sub_exc in enumerate(cause.exceptions, 1):
                rich_console.print(f"[red]  [{i}] {type(sub_exc).__name__}: {sub_exc}[/red]")
        rich_console.print(
            "[dim]Tip: Run with --debug for full error details. "
            "Check GCP auth: gcloud auth application-default login[/dim]"
        )


# ── Entry Point ────────────────────────────────────────────────────

if __name__ == "__main__":
    app()
