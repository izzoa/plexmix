from typing import List, Optional, Union
import logging
import time
import re
from abc import ABC, abstractmethod

import numpy as np

logger = logging.getLogger(__name__)


class EmbeddingProvider(ABC):
    @abstractmethod
    def generate_embedding(self, text: str) -> List[float]:
        pass

    @abstractmethod
    def generate_batch_embeddings(self, texts: List[str]) -> List[List[float]]:
        pass

    @abstractmethod
    def get_dimension(self) -> int:
        pass


class GeminiEmbeddingProvider(EmbeddingProvider):
    def __init__(self, api_key: str, model: str = "gemini-embedding-001"):
        try:
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            self.model_name = model
            self.dimension = 3072
            logger.info(f"Initialized Gemini embedding provider with model {model}")
        except ImportError:
            raise ImportError("google-generativeai not installed. Run: pip install google-generativeai")

    def generate_embedding(self, text: str) -> List[float]:
        import google.generativeai as genai

        max_retries = 5
        base_delay = 2
        backoff_multiplier = 1.5

        for attempt in range(max_retries):
            try:
                result = genai.embed_content(
                    model=f"models/{self.model_name}",
                    content=text,
                    task_type="retrieval_document"
                )
                return result['embedding']
            except Exception as e:
                error_str = str(e)

                if "429" in error_str or "quota" in error_str.lower() or "rate" in error_str.lower():
                    if attempt < max_retries - 1:
                        retry_after = self._extract_retry_delay(error_str)

                        if retry_after:
                            delay = retry_after * backoff_multiplier
                            logger.warning(f"Rate limit hit. Server suggested {retry_after}s, using {delay:.1f}s with backoff...")
                        else:
                            delay = base_delay * (2 ** attempt)
                            logger.warning(f"Rate limit hit. Retrying in {delay}s...")

                        time.sleep(delay)
                        continue
                    else:
                        logger.error(f"Rate limit exceeded after {max_retries} attempts: {e}")
                        raise
                else:
                    logger.error(f"Failed to generate Gemini embedding: {e}")
                    raise

        raise Exception("Max retries exceeded")

    def _extract_retry_delay(self, error_message: str) -> Optional[float]:
        retry_match = re.search(r'retry_delay\s*\{\s*seconds:\s*(\d+)', error_message)
        if retry_match:
            return float(retry_match.group(1))

        retry_after_match = re.search(r'Retry-After:\s*(\d+)', error_message, re.IGNORECASE)
        if retry_after_match:
            return float(retry_after_match.group(1))

        return None

    def generate_batch_embeddings(self, texts: List[str], batch_size: int = 100) -> List[List[float]]:
        import google.generativeai as genai

        embeddings = []
        total_batches = (len(texts) + batch_size - 1) // batch_size
        max_retries = 5
        base_delay = 2
        backoff_multiplier = 1.5

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            batch_num = i // batch_size + 1

            for attempt in range(max_retries):
                try:
                    logger.debug(f"Generating Gemini embeddings batch {batch_num}/{total_batches} ({len(batch)} texts)")

                    for text in batch:
                        embedding = self.generate_embedding(text)
                        embeddings.append(embedding)
                        time.sleep(0.1)

                    logger.debug(f"Completed Gemini batch {batch_num}/{total_batches}")
                    break
                except Exception as e:
                    error_str = str(e)

                    if "429" in error_str or "quota" in error_str.lower() or "rate" in error_str.lower():
                        if attempt < max_retries - 1:
                            retry_after = self._extract_retry_delay(error_str)

                            if retry_after:
                                delay = retry_after * backoff_multiplier
                                logger.warning(f"Rate limit hit on batch {batch_num}. Server suggested {retry_after}s, using {delay:.1f}s with backoff...")
                            else:
                                delay = base_delay * (2 ** attempt)
                                logger.warning(f"Rate limit hit on batch {batch_num} (attempt {attempt + 1}/{max_retries}). Retrying in {delay}s...")

                            time.sleep(delay)
                            continue
                        else:
                            logger.error(f"Rate limit exceeded for batch {batch_num} after {max_retries} attempts: {e}")
                            raise
                    else:
                        logger.error(f"Failed to generate batch embeddings (batch {batch_num}): {e}")
                        raise

        return embeddings

    def get_dimension(self) -> int:
        return self.dimension


class OpenAIEmbeddingProvider(EmbeddingProvider):
    def __init__(self, api_key: str, model: str = "text-embedding-3-small"):
        try:
            from openai import OpenAI
            self.client = OpenAI(api_key=api_key)
            self.model_name = model
            self.dimension = 1536
            logger.info(f"Initialized OpenAI embedding provider with model {model}")
        except ImportError:
            raise ImportError("openai not installed. Run: pip install openai")

    def generate_embedding(self, text: str) -> List[float]:
        try:
            response = self.client.embeddings.create(
                model=self.model_name,
                input=text
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"Failed to generate OpenAI embedding: {e}")
            raise

    def generate_batch_embeddings(self, texts: List[str], batch_size: int = 100) -> List[List[float]]:
        embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            try:
                response = self.client.embeddings.create(
                    model=self.model_name,
                    input=batch
                )
                batch_embeddings = [item.embedding for item in response.data]
                embeddings.extend(batch_embeddings)
                logger.debug(f"Generated {len(batch)} embeddings (batch {i//batch_size + 1})")
            except Exception as e:
                logger.error(f"Failed to generate batch embeddings: {e}")
                raise

        return embeddings

    def get_dimension(self) -> int:
        return self.dimension


class CohereEmbeddingProvider(EmbeddingProvider):
    def __init__(self, api_key: str, model: str = "embed-v4", output_dimension: int = 1024):
        try:
            import cohere
            self.client = cohere.ClientV2(api_key=api_key)
            self.model_name = model
            self.dimension = output_dimension
            logger.info(f"Initialized Cohere embedding provider with model {model} (dim: {output_dimension})")
        except ImportError:
            raise ImportError("cohere not installed. Run: pip install cohere")

    def generate_embedding(self, text: str) -> List[float]:
        try:
            response = self.client.embed(
                model=self.model_name,
                texts=[text],
                input_type="search_document",
                embedding_types=["float"],
                output_dimension=self.dimension
            )
            return response.embeddings.float_[0]
        except Exception as e:
            logger.error(f"Failed to generate Cohere embedding: {e}")
            raise

    def generate_batch_embeddings(self, texts: List[str], batch_size: int = 96) -> List[List[float]]:
        embeddings = []
        total_batches = (len(texts) + batch_size - 1) // batch_size

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            batch_num = i // batch_size + 1

            try:
                logger.debug(f"Generating Cohere embeddings batch {batch_num}/{total_batches} ({len(batch)} texts)")
                response = self.client.embed(
                    model=self.model_name,
                    texts=batch,
                    input_type="search_document",
                    embedding_types=["float"],
                    output_dimension=self.dimension
                )
                embeddings.extend(response.embeddings.float_)
                logger.debug(f"Completed Cohere batch {batch_num}/{total_batches}")
            except Exception as e:
                logger.error(f"Failed to generate batch embeddings (batch {batch_num}): {e}")
                raise

        return embeddings

    def get_dimension(self) -> int:
        return self.dimension


class LocalEmbeddingProvider(EmbeddingProvider):
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        try:
            from sentence_transformers import SentenceTransformer
            self.model = SentenceTransformer(model_name)
            self.dimension = self.model.get_sentence_embedding_dimension()
            logger.info(f"Initialized local embedding provider with model {model_name} (dim: {self.dimension})")
        except ImportError:
            raise ImportError("sentence-transformers not installed. Run: pip install sentence-transformers")

    def generate_embedding(self, text: str) -> List[float]:
        try:
            embedding = self.model.encode(text, convert_to_numpy=True)
            return embedding.tolist()
        except Exception as e:
            logger.error(f"Failed to generate local embedding: {e}")
            raise

    def generate_batch_embeddings(self, texts: List[str], batch_size: int = 32) -> List[List[float]]:
        try:
            embeddings = self.model.encode(
                texts,
                batch_size=batch_size,
                show_progress_bar=True,
                convert_to_numpy=True
            )
            return embeddings.tolist()
        except Exception as e:
            logger.error(f"Failed to generate batch embeddings: {e}")
            raise

    def get_dimension(self) -> int:
        return self.dimension


class EmbeddingGenerator:
    def __init__(self, provider: str, api_key: Optional[str] = None, model: Optional[str] = None):
        self.provider_name = provider.lower()

        if self.provider_name == "gemini":
            if not api_key:
                raise ValueError("API key required for Gemini provider")
            model = model or "gemini-embedding-001"
            self.provider = GeminiEmbeddingProvider(api_key, model)
        elif self.provider_name == "openai":
            if not api_key:
                raise ValueError("API key required for OpenAI provider")
            model = model or "text-embedding-3-small"
            self.provider = OpenAIEmbeddingProvider(api_key, model)
        elif self.provider_name == "cohere":
            if not api_key:
                raise ValueError("API key required for Cohere provider")
            model = model or "embed-v4"
            self.provider = CohereEmbeddingProvider(api_key, model, output_dimension=1024)
        elif self.provider_name == "local":
            model = model or "all-MiniLM-L6-v2"
            self.provider = LocalEmbeddingProvider(model)
        else:
            raise ValueError(f"Unknown provider: {provider}")

    def generate_embedding(self, text: str) -> List[float]:
        return self.provider.generate_embedding(text)

    def generate_batch_embeddings(self, texts: List[str], batch_size: int = 100) -> List[List[float]]:
        return self.provider.generate_batch_embeddings(texts, batch_size)

    def get_dimension(self) -> int:
        return self.provider.get_dimension()


def create_track_text(track_data: dict) -> str:
    title = track_data.get('title', 'Unknown')
    artist = track_data.get('artist', 'Unknown Artist')
    album = track_data.get('album', 'Unknown Album')
    genres = track_data.get('genre', '')
    year = track_data.get('year', '')
    tags = track_data.get('tags', '')
    environments = track_data.get('environments', '')
    instruments = track_data.get('instruments', '')

    text = f"{title} by {artist} from {album}"
    if genres:
        text += f" - {genres}"
    if year:
        text += f" ({year})"
    if tags:
        text += f" | tags: {tags}"
    if environments:
        text += f" | environments: {environments}"
    if instruments:
        text += f" | instruments: {instruments}"

    return text


def embed_track(track_data: dict, generator: EmbeddingGenerator) -> List[float]:
    text = create_track_text(track_data)
    return generator.generate_embedding(text)


def embed_all_tracks(
    tracks: List[dict],
    generator: EmbeddingGenerator,
    batch_size: int = 100
) -> List[List[float]]:
    texts = [create_track_text(track) for track in tracks]
    return generator.generate_batch_embeddings(texts, batch_size)
