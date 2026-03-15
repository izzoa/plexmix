"""Tests for the audio feature analysis module."""
import pytest
from unittest.mock import MagicMock, patch
import numpy as np

from plexmix.audio.analyzer import AudioFeatures, EssentiaAnalyzer


class TestAudioFeatures:
    def test_to_dict(self):
        features = AudioFeatures(
            tempo=120.0,
            key="C",
            scale="major",
            energy_level="high",
            danceability=0.8,
        )
        d = features.to_dict()
        assert d["tempo"] == 120.0
        assert d["key"] == "C"
        assert d["scale"] == "major"
        assert d["energy_level"] == "high"
        assert d["danceability"] == 0.8
        assert d["loudness"] is None

    def test_defaults_are_none(self):
        features = AudioFeatures()
        d = features.to_dict()
        for key, value in d.items():
            assert value is None, f"Expected {key} to be None"


class TestEssentiaAnalyzer:
    def test_import_error_without_essentia(self):
        with patch.dict("sys.modules", {"essentia": None, "essentia.standard": None}):
            with pytest.raises(ImportError, match="essentia not installed"):
                EssentiaAnalyzer()

    def test_classify_energy_high(self):
        assert EssentiaAnalyzer._classify_energy(0.7, 140) == "high"

    def test_classify_energy_medium(self):
        assert EssentiaAnalyzer._classify_energy(0.4, 100) == "medium"

    def test_classify_energy_low(self):
        assert EssentiaAnalyzer._classify_energy(0.1, 60) == "low"

    def test_classify_energy_none(self):
        assert EssentiaAnalyzer._classify_energy(None, 120) is None

    def test_classify_energy_tempo_boost(self):
        # Low energy but very fast tempo should push to medium
        assert EssentiaAnalyzer._classify_energy(0.2, 135) == "medium"

    def test_analyze_with_mocked_essentia(self):
        """Test the analyze method with fully mocked essentia."""
        mock_es = MagicMock()
        mock_audio = np.zeros(44100, dtype=np.float32)  # 1 second of silence

        # Mock MonoLoader
        mock_es.MonoLoader.return_value = MagicMock(return_value=mock_audio)

        # Mock RhythmExtractor2013
        mock_es.RhythmExtractor2013.return_value = MagicMock(return_value=(120.0, [], 0.9, [], []))

        # Mock KeyExtractor
        mock_es.KeyExtractor.return_value = MagicMock(return_value=("C", "major", 0.85))

        # Mock Loudness
        mock_es.Loudness.return_value = MagicMock(return_value=-10.0)

        # Mock Energy
        mock_es.Energy.return_value = MagicMock(return_value=0.5)

        # Mock SpectralCentroidTime
        mock_es.SpectralCentroidTime.return_value = MagicMock(return_value=2000.0)

        # Mock ZeroCrossingRate
        mock_es.ZeroCrossingRate.return_value = MagicMock(return_value=0.1)

        # Mock Danceability
        mock_es.Danceability.return_value = MagicMock(return_value=(0.75, []))

        # Mock MFCC-related: Windowing, Spectrum, MFCC, FrameGenerator
        mock_es.Windowing.return_value = MagicMock(return_value=np.zeros(2048))
        mock_es.Spectrum.return_value = MagicMock(return_value=np.zeros(1025))
        mock_es.MFCC.return_value = MagicMock(return_value=(np.zeros(40), np.zeros(13)))
        mock_es.FrameGenerator.return_value = [np.zeros(2048)]

        with patch("plexmix.audio.analyzer.EssentiaAnalyzer.__init__", return_value=None):
            analyzer = EssentiaAnalyzer.__new__(EssentiaAnalyzer)
            analyzer.es = mock_es

            features = analyzer.analyze("/fake/path.mp3")

            assert features.tempo == 120.0
            assert features.key == "C"
            assert features.scale == "major"
            assert features.danceability == 0.75
            assert features.energy_level is not None
