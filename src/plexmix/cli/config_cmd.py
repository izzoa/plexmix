import typer
from typing import Optional
from rich.console import Console
from rich.table import Table

from ..config.settings import Settings, get_config_path
from ..config import credentials
from ..plex.client import PlexClient
from ..ai import LOCAL_LLM_MODELS, LOCAL_LLM_DEFAULT_MODEL

config_app = typer.Typer(name="config", help="Configuration management")
console = Console()


@config_app.command("init")
def config_init() -> None:
    """Interactive setup wizard for PlexMix configuration."""
    console.print("[bold green]PlexMix Setup Wizard[/bold green]")
    console.print("This wizard will help you configure PlexMix.\n")

    plex_url = typer.prompt("Plex server URL", default="http://localhost:32400")
    plex_token = typer.prompt("Plex token", hide_input=True)

    credentials.store_plex_token(plex_token)

    plex_client = PlexClient(plex_url, plex_token)
    if not plex_client.connect():
        console.print("[red]Failed to connect to Plex server.[/red]\n")
        console.print("[yellow]Troubleshooting tips:[/yellow]")
        console.print("  1. Verify your Plex token is correct (no extra spaces)")
        console.print("  2. Check if your Plex server URL is accessible")
        console.print("  3. Try using https:// instead of http://")
        console.print("  4. If using a remote server, ensure the port is correct")
        console.print("  5. Check Plex server settings for 'Require authentication'")
        console.print("\n[dim]See logs above for detailed error information[/dim]")
        raise typer.Exit(1)

    libraries = plex_client.get_music_libraries()
    if not libraries:
        console.print("[red]No music libraries found on Plex server.[/red]")
        raise typer.Exit(1)

    console.print("\nAvailable music libraries:")
    for idx, lib in enumerate(libraries):
        console.print(f"  {idx + 1}. {lib}")

    lib_choice = typer.prompt("Select library number", type=int, default=1)
    library_name = libraries[lib_choice - 1]

    ai_model = None
    local_mode = "builtin"
    local_endpoint: Optional[str] = None
    local_auth_token: Optional[str] = None

    console.print("\n[bold]Provider Configuration[/bold]")
    console.print("PlexMix supports multiple AI and embedding providers:\n")
    console.print("  1. Google Gemini (default) - Single API key for both AI and embeddings")
    console.print("  2. OpenAI - GPT models and embeddings")
    console.print("  3. Anthropic Claude - AI playlist generation only (no embeddings)")
    console.print("  4. Cohere - Command R models and embeddings")
    console.print("  5. Local embeddings - Free, offline (no API key needed)")
    console.print(
        "  6. Local LLM - Offline playlist generation via Gemma/Yarn presets or your own endpoint\n"
    )
    console.print(
        "[dim]Note: Anthropic does not provide embeddings, so you'll need Gemini, OpenAI,"
        " Cohere, or local.[/dim]\n"
    )

    use_gemini = typer.confirm("Use Google Gemini? (recommended)", default=True)

    google_api_key = None
    if use_gemini:
        google_api_key = typer.prompt("Google Gemini API key", hide_input=True)
        credentials.store_google_api_key(google_api_key)

    embedding_provider = "gemini" if use_gemini else None
    ai_provider = "gemini" if use_gemini else None

    use_openai = typer.confirm("\nConfigure OpenAI?", default=False)
    openai_key = None
    if use_openai:
        openai_key = typer.prompt("OpenAI API key", hide_input=True)
        credentials.store_openai_api_key(openai_key)

        if not use_gemini:
            console.print("\nOpenAI will be used for:")
            use_openai_embeddings = typer.confirm("  - Embeddings?", default=True)
            use_openai_ai = typer.confirm("  - Playlist generation?", default=True)

            if use_openai_embeddings:
                embedding_provider = "openai"
            if use_openai_ai:
                ai_provider = "openai"

    use_cohere = typer.confirm("\nConfigure Cohere?", default=False)
    cohere_key = None
    if use_cohere:
        cohere_key = typer.prompt("Cohere API key", hide_input=True)
        credentials.store_cohere_api_key(cohere_key)

        if not use_gemini and not use_openai:
            console.print("\nCohere will be used for:")
            use_cohere_embeddings = typer.confirm("  - Embeddings?", default=True)
            use_cohere_ai = typer.confirm("  - Playlist generation?", default=True)

            if use_cohere_embeddings:
                embedding_provider = "cohere"
            if use_cohere_ai:
                ai_provider = "cohere"

    use_anthropic = typer.confirm("\nConfigure Anthropic Claude?", default=False)
    if use_anthropic:
        anthropic_key = typer.prompt("Anthropic API key", hide_input=True)
        credentials.store_anthropic_api_key(anthropic_key)

        if not ai_provider:
            ai_provider = "claude"

        if not embedding_provider:
            console.print(
                "\n[yellow]Anthropic selected for AI, but does not provide embeddings.[/yellow]"
            )
            console.print("Choose an embedding provider:")
            console.print("  1. Google Gemini (3072 dimensions)")
            console.print("  2. OpenAI (1536 dimensions)")
            console.print("  3. Cohere (1024 dimensions)")
            console.print("  4. Local (384 dimensions, free, offline)")

            emb_choice = typer.prompt(
                "\nEmbedding provider", type=int, default=4, show_default=True
            )

            if emb_choice == 1:
                if not google_api_key:
                    google_api_key = typer.prompt("Google Gemini API key", hide_input=True)
                    credentials.store_google_api_key(google_api_key)
                embedding_provider = "gemini"
            elif emb_choice == 2:
                if not openai_key:
                    openai_key = typer.prompt("OpenAI API key", hide_input=True)
                    credentials.store_openai_api_key(openai_key)
                embedding_provider = "openai"
            elif emb_choice == 3:
                if not cohere_key:
                    cohere_key = typer.prompt("Cohere API key", hide_input=True)
                    credentials.store_cohere_api_key(cohere_key)
                embedding_provider = "cohere"
            else:
                embedding_provider = "local"

    use_local_llm = typer.confirm("\nUse Local LLM (offline or custom endpoint)?", default=False)
    if use_local_llm:
        ai_provider = "local"
        console.print("\nLocal LLM presets:")
        preset_items = list(LOCAL_LLM_MODELS.items())
        for idx, (model_id, meta) in enumerate(preset_items, start=1):
            console.print(f"  {idx}. {meta['display_name']} [{model_id}] - {meta['capabilities']}")
        model_prompt = "Enter local model (or number)"
        model_choice = typer.prompt(model_prompt, default=LOCAL_LLM_DEFAULT_MODEL)
        if model_choice.isdigit():
            idx = int(model_choice)
            if 1 <= idx <= len(preset_items):
                model_choice = preset_items[idx - 1][0]
        ai_model = model_choice

        use_endpoint = typer.confirm("Point to a custom local API endpoint instead?", default=False)
        if use_endpoint:
            local_mode = "endpoint"
            local_endpoint = typer.prompt(
                "Endpoint URL", default="http://localhost:11434/v1/chat/completions"
            )
            token_input = typer.prompt(
                "Endpoint auth token (optional)", hide_input=True, default=""
            )
            local_auth_token = token_input or None
        else:
            local_mode = "builtin"
            local_endpoint = None
            local_auth_token = None
            console.print(
                "[dim]Models are cached on-demand. Use the UI settings page to pre-download"
                " if needed.[/dim]"
            )

    if not embedding_provider:
        console.print(
            "\n[yellow]No embedding provider selected."
            " Using local embeddings (free, offline).[/yellow]"
        )
        embedding_provider = "local"

    if not ai_provider:
        console.print("\n[red]Error: No AI provider configured for playlist generation.[/red]")
        console.print(
            "You must configure at least one of: Gemini, OpenAI, Cohere, Anthropic, or Local LLM"
        )
        raise typer.Exit(1)

    settings = Settings()
    settings.plex.url = plex_url
    settings.plex.library_name = library_name
    settings.embedding.default_provider = embedding_provider
    settings.ai.default_provider = ai_provider
    settings.ai.model = ai_model
    settings.ai.local_mode = local_mode
    settings.ai.local_endpoint = local_endpoint
    settings.ai.local_auth_token = local_auth_token

    config_path = get_config_path()
    settings.save_to_file(str(config_path))

    console.print(f"\n[green]Configuration saved to {config_path}[/green]")

    if typer.confirm(
        "\nRun initial sync now? (May take 10-30 minutes for large libraries)", default=True
    ):
        try:
            from .sync_cmd import sync_incremental

            sync_incremental()
        except typer.Exit as e:
            if e.exit_code == 130:  # KeyboardInterrupt
                console.print("\n[yellow]You can resume the sync later with:[/yellow]")
                console.print("  plexmix sync")
            raise
    else:
        console.print("\nYou can run sync later with: plexmix sync")


@config_app.command("test")
def config_test() -> None:
    """Test Plex server connection."""
    settings = Settings()

    console.print("\n[bold cyan]Testing Plex Connection[/bold cyan]\n")

    if not settings.plex.url:
        console.print("[red]No Plex URL configured.[/red]")
        console.print("Run 'plexmix config init' to set up your configuration.")
        raise typer.Exit(1)

    plex_token = credentials.get_plex_token()
    if not plex_token:
        console.print("[red]No Plex token found.[/red]")
        console.print("Run 'plexmix config init' to set up your configuration.")
        raise typer.Exit(1)

    console.print(f"[dim]Server URL:[/dim] {settings.plex.url}")
    console.print(f"[dim]Token length:[/dim] {len(plex_token)} characters")
    console.print()

    plex_client = PlexClient(settings.plex.url, plex_token)

    console.print("Attempting to connect...")
    if plex_client.connect():
        assert plex_client.server is not None
        console.print(
            f"[green]✓ Successfully connected to: {plex_client.server.friendlyName}[/green]"
        )
        console.print(f"  Version: {plex_client.server.version}")
        console.print(f"  Platform: {plex_client.server.platform}")

        if settings.plex.library_name:
            console.print(f"\nTesting library access: {settings.plex.library_name}")
            if plex_client.select_library(settings.plex.library_name):
                console.print(
                    f"[green]✓ Library '{settings.plex.library_name}' is accessible[/green]"
                )
            else:
                console.print(f"[red]✗ Library '{settings.plex.library_name}' not found[/red]")

        console.print("\n[green]Connection test passed![/green]")
    else:
        console.print("[red]✗ Connection failed[/red]")
        console.print("\n[yellow]Troubleshooting:[/yellow]")
        console.print("  • Double-check your Plex token")
        console.print("  • Try using https:// instead of http://")
        console.print("  • Verify the server URL and port")
        console.print("  • Check if Plex server is running")
        raise typer.Exit(1)


@config_app.command("show")
def config_show() -> None:
    """Show current PlexMix configuration."""
    config_path = get_config_path()
    if not config_path.exists():
        console.print("[yellow]No configuration found. Run 'plexmix config init' first.[/yellow]")
        raise typer.Exit(1)

    settings = Settings.load_from_file(str(config_path))

    table = Table(title="PlexMix Configuration")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Plex URL", settings.plex.url or "Not set")
    table.add_row("Library", settings.plex.library_name or "Not set")
    table.add_row("Database Path", settings.database.path)
    table.add_row("AI Provider", settings.ai.default_provider)
    table.add_row("Embedding Provider", settings.embedding.default_provider)
    table.add_row("Playlist Length", str(settings.playlist.default_length))

    console.print(table)
