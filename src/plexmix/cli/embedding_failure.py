"""What a CLI command says when the embedding provider cannot start.

``plexmix create``, ``embeddings generate``, ``doctor`` and ``tags generate`` build the
embedding provider through ``build_embedding_generator_with_reason``. That returns the
provider, or a typed :class:`~plexmix.services.providers.ProviderFailure`. This module turns
the failure into the lines a terminal user needs: the specific cause and one next step.
It says that an API key is required only when the cause is a missing key.
"""

from typing import List, Optional

from rich.console import Console
from rich.markup import escape

from ..services.providers import ProviderFailure
from ..services.readiness import LOCAL_REMEDY, Cause, describe_failure

API_KEY_REQUIRED_LINE = "API key required for embedding provider."
CONFIG_INIT_COMMAND = "plexmix config init"
CHOOSE_PROVIDER_STEP = f"choose another embedding provider: {CONFIG_INIT_COMMAND}"


def _next_step(failure: ProviderFailure) -> str:
    """The one thing to try, in plain text."""
    if failure.cause == Cause.MISSING_DEPENDENCY and failure.provider.strip().lower() == "local":
        return f"{LOCAL_REMEDY}, or {CHOOSE_PROVIDER_STEP}"
    if failure.cause == Cause.MODEL_LOAD_FAILED:
        return f"See the log for details, try a smaller model, or {CHOOSE_PROVIDER_STEP}"
    return f"Check the embedding settings: {CONFIG_INIT_COMMAND}"


def embedding_failure_lines(failure: Optional[ProviderFailure]) -> List[str]:
    """Plain text lines (no Rich markup) that say why the embedding provider cannot start.

    ``None`` and a missing API key give today's two lines. Any other cause gives the title
    the web UI uses for it, then the failure's detail (already free of any key, and capped)
    when there is one, then one next step.
    """
    if failure is None or failure.cause == Cause.MISSING_API_KEY:
        return [API_KEY_REQUIRED_LINE, f"Run: {CONFIG_INIT_COMMAND}"]

    lines = [describe_failure("embeddings", failure)["title"]]
    detail = (failure.detail or "").strip()
    if detail:
        lines.append(detail)
    lines.append(_next_step(failure))
    return lines


def print_embedding_failure(
    console: Console, failure: Optional[ProviderFailure], style: str = "red"
) -> None:
    """Print :func:`embedding_failure_lines` in ``style``.

    Each line goes through ``rich.markup.escape``: a next step holds ``plexmix[local]`` and a
    detail can hold ``[Errno 32]``, and Rich would read those as markup.
    """
    for line in embedding_failure_lines(failure):
        console.print(f"[{style}]{escape(line)}[/{style}]")
