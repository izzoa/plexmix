from abc import ABC, abstractmethod
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class AIProvider(ABC):
    def __init__(self, api_key: str, model: str, temperature: float = 0.7):
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.provider_name = self.__class__.__name__.replace("Provider", "")

    @abstractmethod
    def complete(
        self,
        prompt: str,
        temperature: Optional[float] = None,
        max_tokens: int = 4096,
        timeout: int = 30
    ) -> str:
        """
        Send a prompt to the AI provider and return the text response.

        Args:
            prompt: The text prompt to send
            temperature: Override the default temperature (0-1)
            max_tokens: Maximum tokens in the response
            timeout: Request timeout in seconds

        Returns:
            The text response from the AI provider
        """
        pass
