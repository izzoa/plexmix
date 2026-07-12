from dataclasses import dataclass, asdict
from typing import Optional, List
import logging

logger = logging.getLogger(__name__)


@dataclass
class AudioFeatures:
    """Extracted audio features from a track."""

    tempo: Optional[float] = None
    tempo_confidence: Optional[float] = None
    key: Optional[str] = None
    scale: Optional[str] = None
    key_confidence: Optional[float] = None
    loudness: Optional[float] = None
    energy: Optional[float] = None
    energy_level: Optional[str] = None
    danceability: Optional[float] = None
    spectral_centroid: Optional[float] = None
    mfcc: Optional[List[float]] = None
    zero_crossing_rate: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)


class EssentiaAnalyzer:
    """Audio feature extractor using Essentia."""

    def __init__(self) -> None:
        try:
            import essentia.standard as es

            self.es = es
        except ImportError:
            raise ImportError(
                "essentia not installed. Run: pip install essentia " "or: poetry install -E audio"
            )

    def analyze(self, file_path: str, duration_limit: int = 0) -> AudioFeatures:
        """Extract DSP features from an audio file.

        Args:
            file_path: Path to the audio file.
            duration_limit: Seconds of audio to analyze (0 = full track).
        """
        try:
            loader_kwargs = {"filename": file_path, "sampleRate": 44100}
            audio = self.es.MonoLoader(**loader_kwargs)()

            if duration_limit > 0:
                max_samples = duration_limit * 44100
                if len(audio) > max_samples:
                    audio = audio[:max_samples]

            # Rhythm: tempo detection
            bpm, tempo_confidence = self._extract_tempo(audio)

            # Key detection
            key, scale, key_confidence = self._extract_key(audio)

            # Energy & loudness
            loudness = float(self.es.Loudness()(audio))
            raw_energy = float(self.es.Energy()(audio))
            # Essentia's Energy() is the sum of squares over the whole signal, so it
            # grows with track length. Convert to RMS (sqrt of the mean square), a
            # length-independent 0-1 loudness proxy. The previous code divided the
            # total energy by the sample count, yielding mean-square values so tiny
            # that every track scored "low" and "high" was unreachable.
            mean_square = raw_energy / max(len(audio), 1)
            energy = min(max(mean_square**0.5, 0.0), 1.0)

            # Spectral features
            centroid = float(self.es.SpectralCentroidTime()(audio))
            zcr = float(self.es.ZeroCrossingRate()(audio))

            # MFCCs (compute on spectrum of frames, average across frames)
            mfcc_coeffs = self._extract_mfcc(audio)

            # Danceability. Essentia returns this on a 0-3 scale; normalize to 0-1
            # to match how it is consumed (playlist danceability_min filter and
            # embedding text both assume a 0-1 range).
            danceability_val, _ = self.es.Danceability()(audio)
            danceability = min(max(float(danceability_val) / 3.0, 0.0), 1.0)

            # Derive energy level
            energy_level = self._classify_energy(energy, bpm)

            return AudioFeatures(
                tempo=bpm,
                tempo_confidence=tempo_confidence,
                key=key,
                scale=scale,
                key_confidence=key_confidence,
                loudness=loudness,
                energy=energy,
                energy_level=energy_level,
                danceability=danceability,
                spectral_centroid=centroid,
                mfcc=mfcc_coeffs,
                zero_crossing_rate=zcr,
            )

        except Exception as e:
            logger.error(f"Failed to analyze audio file {file_path}: {e}")
            raise

    def _extract_tempo(self, audio: object) -> tuple:
        """Extract BPM and confidence."""
        try:
            result = self.es.RhythmExtractor2013(method="multifeature")(audio)
            bpm = float(result[0])
            confidence = float(result[2]) if len(result) > 2 else None
            return bpm, confidence
        except Exception as e:
            logger.warning(f"Tempo extraction failed: {e}")
            return None, None

    def _extract_key(self, audio: object) -> tuple:
        """Extract musical key, scale, and confidence."""
        try:
            key, scale, strength = self.es.KeyExtractor()(audio)
            return str(key), str(scale), float(strength)
        except Exception as e:
            logger.warning(f"Key extraction failed: {e}")
            return None, None, None

    def _extract_mfcc(self, audio: object) -> Optional[List[float]]:
        """Extract averaged MFCC coefficients across frames."""
        try:
            import numpy as np

            frame_size = 2048
            hop_size = 1024
            windowing = self.es.Windowing(type="hann")
            spectrum = self.es.Spectrum()
            mfcc = self.es.MFCC(numberCoefficients=13)

            all_mfcc = []
            for frame in self.es.FrameGenerator(audio, frameSize=frame_size, hopSize=hop_size):
                windowed = windowing(frame)
                spec = spectrum(windowed)
                _, mfcc_coeffs = mfcc(spec)
                all_mfcc.append(mfcc_coeffs)

            if all_mfcc:
                avg_mfcc = np.mean(all_mfcc, axis=0)
                return [float(x) for x in avg_mfcc]
            return None
        except Exception as e:
            logger.warning(f"MFCC extraction failed: {e}")
            return None

    @staticmethod
    def _classify_energy(energy: Optional[float], bpm: Optional[float]) -> Optional[str]:
        """Classify energy level based on energy value and tempo."""
        if energy is None:
            return None

        # Combine energy and tempo signals. With the RMS energy fix, typical music
        # lands in the ~0.1-0.4 range (was ~0 under the old mean-square math, which
        # made every track "low"), so these thresholds are now actually reachable.
        score = energy
        if bpm is not None:
            if bpm >= 130:
                score += 0.2
            elif bpm >= 100:
                score += 0.1

        if score >= 0.6:
            return "high"
        elif score >= 0.3:
            return "medium"
        return "low"
