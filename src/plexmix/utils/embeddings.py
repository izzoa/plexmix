from typing import List, Optional, Union, Dict
import logging
import time
import re
import os
import multiprocessing as mp
import threading
import atexit
from abc import ABC, abstractmethod

import numpy as np

logger = logging.getLogger(__name__)

LOCAL_EMBEDDING_MODELS: Dict[str, Dict[str, Union[int, bool]]] = {
    "all-MiniLM-L6-v2": {"dimension": 384, "trust_remote_code": False},
    "mixedbread-ai/mxbai-embed-large-v1": {"dimension": 1024, "trust_remote_code": False},
    "google/embeddinggemma-300m": {"dimension": 768, "trust_remote_code": False},
    "nomic-ai/nomic-embed-text-v1.5": {"dimension": 768, "trust_remote_code": True},
}

LOCAL_EMBEDDING_DEVICE = os.getenv("PLEXMIX_LOCAL_EMBEDDING_DEVICE", "cpu")


class EmbeddingProvider(ABC):
    def __init__(self):
        self.provider_name = self.__class__.__name__.replace("EmbeddingProvider", "")
    
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
        super().__init__()
        try:
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            self.model_name = model
            self.dimension = 3072
            logger.info(f"[{self.provider_name}] Initialized embedding provider with model {model}")
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
                            logger.warning(f"[{self.provider_name}] Rate limit hit. Server suggested {retry_after}s, using {delay:.1f}s with backoff...")
                        else:
                            delay = base_delay * (2 ** attempt)
                            logger.warning(f"[{self.provider_name}] Rate limit hit. Retrying in {delay}s...")

                        time.sleep(delay)
                        continue
                    else:
                        logger.error(f"[{self.provider_name}] Rate limit exceeded after {max_retries} attempts: {e}")
                        raise
                else:
                    logger.error(f"[{self.provider_name}] Failed to generate embedding: {e}")
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
                    logger.debug(f"[{self.provider_name}] Generating embeddings batch {batch_num}/{total_batches} ({len(batch)} texts)")

                    for text in batch:
                        embedding = self.generate_embedding(text)
                        embeddings.append(embedding)
                        time.sleep(0.1)

                    logger.debug(f"[{self.provider_name}] Completed batch {batch_num}/{total_batches}")
                    break
                except Exception as e:
                    error_str = str(e)

                    if "429" in error_str or "quota" in error_str.lower() or "rate" in error_str.lower():
                        if attempt < max_retries - 1:
                            retry_after = self._extract_retry_delay(error_str)

                            if retry_after:
                                delay = retry_after * backoff_multiplier
                                logger.warning(f"[{self.provider_name}] Rate limit hit on batch {batch_num}. Server suggested {retry_after}s, using {delay:.1f}s with backoff...")
                            else:
                                delay = base_delay * (2 ** attempt)
                                logger.warning(f"[{self.provider_name}] Rate limit hit on batch {batch_num} (attempt {attempt + 1}/{max_retries}). Retrying in {delay}s...")

                            time.sleep(delay)
                            continue
                        else:
                            logger.error(f"[{self.provider_name}] Rate limit exceeded for batch {batch_num} after {max_retries} attempts: {e}")
                            raise
                    else:
                        logger.error(f"[{self.provider_name}] Failed to generate batch embeddings (batch {batch_num}): {e}")
                        raise

        return embeddings

    def get_dimension(self) -> int:
        return self.dimension


class OpenAIEmbeddingProvider(EmbeddingProvider):
    def __init__(self, api_key: str, model: str = "text-embedding-3-small"):
        super().__init__()
        try:
            from openai import OpenAI
            self.client = OpenAI(api_key=api_key)
            self.model_name = model
            self.dimension = 1536
            logger.info(f"[{self.provider_name}] Initialized embedding provider with model {model}")
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
            logger.error(f"[{self.provider_name}] Failed to generate embedding: {e}")
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
                logger.debug(f"[{self.provider_name}] Generated {len(batch)} embeddings (batch {i//batch_size + 1})")
            except Exception as e:
                logger.error(f"[{self.provider_name}] Failed to generate batch embeddings: {e}")
                raise

        return embeddings

    def get_dimension(self) -> int:
        return self.dimension


class CohereEmbeddingProvider(EmbeddingProvider):
    def __init__(self, api_key: str, model: str = "embed-v4", output_dimension: int = 1024):
        super().__init__()
        try:
            import cohere
            self.client = cohere.ClientV2(api_key=api_key)
            self.model_name = model
            self.dimension = output_dimension
            logger.info(f"[{self.provider_name}] Initialized embedding provider with model {model} (dim: {output_dimension})")
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
            logger.error(f"[{self.provider_name}] Failed to generate embedding: {e}")
            raise

    def generate_batch_embeddings(self, texts: List[str], batch_size: int = 96) -> List[List[float]]:
        embeddings = []
        total_batches = (len(texts) + batch_size - 1) // batch_size

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            batch_num = i // batch_size + 1

            try:
                logger.debug(f"[{self.provider_name}] Generating embeddings batch {batch_num}/{total_batches} ({len(batch)} texts)")
                response = self.client.embed(
                    model=self.model_name,
                    texts=batch,
                    input_type="search_document",
                    embedding_types=["float"],
                    output_dimension=self.dimension
                )
                embeddings.extend(response.embeddings.float_)
                logger.debug(f"[{self.provider_name}] Completed batch {batch_num}/{total_batches}")
            except Exception as e:
                logger.error(f"[{self.provider_name}] Failed to generate batch embeddings (batch {batch_num}): {e}")
                raise

        return embeddings

    def get_dimension(self) -> int:
        return self.dimension


class LocalEmbeddingProvider(EmbeddingProvider):
    worker_cache: Dict[str, Dict[str, any]] = {}

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        super().__init__()
        try:
            from sentence_transformers import SentenceTransformer  # noqa: F401
        except ImportError:
            raise ImportError("sentence-transformers not installed. Run: pip install sentence-transformers")

        model_config = LOCAL_EMBEDDING_MODELS.get(model_name, {})
        trust_remote_code = bool(model_config.get("trust_remote_code", False))
        device = LOCAL_EMBEDDING_DEVICE
        cache_key = f"{model_name}-{trust_remote_code}-{device}"

        cached = self.worker_cache.get(cache_key)
        if cached and cached["process"].is_alive():
            logger.info(f"[{self.provider_name}] Reusing cached local worker for {model_name} on {device}")
            self.worker_conn = cached["conn"]
            self.worker_process = cached["process"]
            self.dimension = cached["dimension"]
            self.worker_lock = cached["lock"]
        else:
            if cached:
                logger.warning(f"[{self.provider_name}] Cached worker for {model_name} is not alive. Restarting...")

            ctx = mp.get_context("spawn")
            parent_conn, child_conn = ctx.Pipe()
            worker = ctx.Process(
                target=_local_embedding_worker,
                args=(model_name, trust_remote_code, device, child_conn),
                daemon=True,
            )
            worker.start()

            try:
                handshake = parent_conn.recv()
            except EOFError:
                raise RuntimeError(
                    f"Failed to start local embedding worker for {model_name}. "
                    "See logs for details."
                )

            if handshake.get("status") != "ready":
                error = handshake.get("error", "Unknown error")
                raise RuntimeError(f"Failed to load local model {model_name}: {error}")

            self.worker_conn = parent_conn
            self.worker_process = worker
            self.dimension = int(handshake.get("dimension", 0)) or int(model_config.get("dimension", 384))
            self.worker_lock = threading.Lock()
            self.worker_cache[cache_key] = {
                "conn": self.worker_conn,
                "process": self.worker_process,
                "dimension": self.dimension,
                "lock": self.worker_lock,
            }

            logger.info(
                f"[{self.provider_name}] Local embedding worker ready for {model_name} "
                f"(dim: {self.dimension}, device={device})"
            )

            atexit.register(self._shutdown_worker, cache_key)

    def generate_embedding(self, text: str) -> List[float]:
        try:
            with self.worker_lock:
                self.worker_conn.send({"cmd": "embed", "texts": [text]})
                result = self.worker_conn.recv()
            if result.get("status") != "ok":
                raise RuntimeError(result.get("error", "Unknown error generating embedding"))
            embedding = np.array(result["embeddings"][0], dtype=np.float32)
            return self._truncate_vector(embedding).tolist()
        except Exception as e:
            logger.error(f"[{self.provider_name}] Failed to generate embedding: {e}")
            raise

    def generate_batch_embeddings(self, texts: List[str], batch_size: int = 32) -> List[List[float]]:
        try:
            with self.worker_lock:
                self.worker_conn.send({"cmd": "embed", "texts": texts, "batch_size": batch_size})
                result = self.worker_conn.recv()
            if result.get("status") != "ok":
                raise RuntimeError(result.get("error", "Unknown error generating batch embeddings"))
            embeddings = np.array(result["embeddings"], dtype=np.float32)
            embeddings = self._truncate_batch(embeddings)
            return embeddings.tolist()
        except Exception as e:
            logger.error(f"[{self.provider_name}] Failed to generate batch embeddings: {e}")
            raise

    def get_dimension(self) -> int:
        return self.dimension

    def _truncate_vector(self, vector: np.ndarray) -> np.ndarray:
        if self.dimension and vector.shape[-1] > self.dimension:
            return vector[: self.dimension]
        return vector

    def _truncate_batch(self, embeddings: np.ndarray) -> np.ndarray:
        if self.dimension and embeddings.shape[-1] > self.dimension:
            return embeddings[:, : self.dimension]
        return embeddings

    def _shutdown_worker(self, cache_key: str):
        worker_info = self.worker_cache.get(cache_key)
        if not worker_info:
            return
        conn = worker_info.get("conn")
        process = worker_info.get("process")
        try:
            conn.send({"cmd": "shutdown"})
        except Exception:
            pass
        if process.is_alive():
            process.join(timeout=2)
        self.worker_cache.pop(cache_key, None)


def _local_embedding_worker(model_name: str, trust_remote_code: bool, device: str, conn):
    try:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(model_name, trust_remote_code=trust_remote_code, device=device)
        dimension = model.get_sentence_embedding_dimension()
        conn.send({"status": "ready", "dimension": dimension})

        while True:
            message = conn.recv()
            cmd = message.get("cmd")
            if cmd == "shutdown":
                break
            if cmd == "embed":
                texts = message.get("texts", [])
                batch_size = message.get("batch_size", 32)
                embeddings = model.encode(
                    texts,
                    batch_size=batch_size,
                    show_progress_bar=False,
                    convert_to_numpy=True,
                )
                conn.send({"status": "ok", "embeddings": embeddings.tolist()})
            else:
                conn.send({"status": "error", "error": f"Unknown command: {cmd}"})
    except Exception as e:
        logger.error(f"Local embedding worker failed for {model_name}: {e}", exc_info=True)
        try:
            conn.send({"status": "error", "error": str(e)})
        except Exception:
            pass


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
