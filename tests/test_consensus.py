"""Tests for the multi-LLM consensus engine.

Covers AIDC-01 through AIDC-04 requirements:
- AIDC-01: Both LLMs queried with adversarial prompts
- AIDC-02: Veto consensus -- both must agree on action
- AIDC-03: Confidence >= 0.6 threshold
- AIDC-04: Disagreement defaults to HOLD with logging
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# Task 1 tests: Schema validation and prompt building
# ---------------------------------------------------------------------------


class TestTradingAnalysisSchema:
    """Test Pydantic schema validation for TradingAnalysis."""

    def test_valid_trading_analysis(self):
        """TradingAnalysis validates a well-formed structured output."""
        from src.models import TradingAnalysis, TradeRecommendation

        rec = TradeRecommendation(
            action="BUY",
            symbol="RXRX",
            confidence=0.8,
            stop_loss_pct=0.15,
            reasoning="Strong fundamentals and catalyst upcoming.",
        )
        analysis = TradingAnalysis(
            market_assessment="Bullish micro-cap environment.",
            recommendations=[rec],
        )
        assert analysis.recommendations[0].action == "BUY"
        assert analysis.recommendations[0].confidence == 0.8

    def test_rejects_confidence_above_one(self):
        """TradingAnalysis rejects confidence > 1.0."""
        from src.models import TradeRecommendation

        with pytest.raises(ValidationError):
            TradeRecommendation(
                action="BUY",
                symbol="RXRX",
                confidence=1.5,
                stop_loss_pct=0.15,
                reasoning="Invalid confidence.",
            )

    def test_rejects_confidence_below_zero(self):
        """TradingAnalysis rejects confidence < 0.0."""
        from src.models import TradeRecommendation

        with pytest.raises(ValidationError):
            TradeRecommendation(
                action="BUY",
                symbol="RXRX",
                confidence=-0.1,
                stop_loss_pct=0.15,
                reasoning="Invalid confidence.",
            )

    def test_rejects_stop_loss_above_fifty_pct(self):
        """TradingAnalysis rejects stop_loss_pct > 0.50."""
        from src.models import TradeRecommendation

        with pytest.raises(ValidationError):
            TradeRecommendation(
                action="BUY",
                symbol="RXRX",
                confidence=0.8,
                stop_loss_pct=0.55,
                reasoning="Invalid stop loss.",
            )

    def test_rejects_stop_loss_below_one_pct(self):
        """TradingAnalysis rejects stop_loss_pct < 0.01."""
        from src.models import TradeRecommendation

        with pytest.raises(ValidationError):
            TradeRecommendation(
                action="BUY",
                symbol="RXRX",
                confidence=0.8,
                stop_loss_pct=0.005,
                reasoning="Invalid stop loss.",
            )


class TestBuildUserPrompt:
    """Test the user prompt builder."""

    def test_prompt_includes_positions(self, sample_positions):
        """build_user_prompt includes portfolio positions in output string."""
        from src.prompts import build_user_prompt

        prompt = build_user_prompt(
            positions=sample_positions,
            buying_power=5000.0,
            candidates=["RXRX", "ATOS"],
        )
        assert "RXRX" in prompt
        assert "ATOS" in prompt
        assert "100" in prompt  # shares of RXRX

    def test_prompt_includes_buying_power(self, sample_positions):
        """build_user_prompt includes buying_power in output string."""
        from src.prompts import build_user_prompt

        prompt = build_user_prompt(
            positions=sample_positions,
            buying_power=5000.0,
            candidates=["RXRX"],
        )
        assert "5000" in prompt or "5,000" in prompt

    def test_prompt_includes_candidates(self, sample_positions):
        """build_user_prompt includes candidate tickers in output string."""
        from src.prompts import build_user_prompt

        prompt = build_user_prompt(
            positions=sample_positions,
            buying_power=5000.0,
            candidates=["ABCD", "EFGH", "IJKL"],
        )
        assert "ABCD" in prompt
        assert "EFGH" in prompt
        assert "IJKL" in prompt


# ---------------------------------------------------------------------------
# Task 2 tests: Consensus engine (RED -- will fail until consensus.py exists)
# ---------------------------------------------------------------------------


class TestEvaluateConsensus:
    """Test the veto consensus logic."""

    def test_approve_when_both_agree_buy_high_confidence(
        self, sample_bull_analysis, sample_bear_analysis
    ):
        """Both models agree BUY on RXRX with confidence >= 0.6 -> approved."""
        from src.consensus import evaluate_consensus

        approved, disagreed = evaluate_consensus(
            sample_bull_analysis, sample_bear_analysis, min_confidence=0.6
        )
        symbols = [t.symbol for t in approved]
        assert "RXRX" in symbols

    def test_reject_when_actions_disagree(
        self, sample_bull_analysis, sample_bear_analysis
    ):
        """Bull says HOLD, bear says SELL on ATOS -> disagreed."""
        from src.consensus import evaluate_consensus

        approved, disagreed = evaluate_consensus(
            sample_bull_analysis, sample_bear_analysis, min_confidence=0.6
        )
        approved_symbols = [t.symbol for t in approved]
        assert "ATOS" not in approved_symbols
        assert "ATOS" in disagreed

    def test_reject_when_confidence_below_threshold(self):
        """Both agree BUY but min confidence < 0.6 -> rejected."""
        from src.models import TradingAnalysis, TradeRecommendation
        from src.consensus import evaluate_consensus

        bull = TradingAnalysis(
            market_assessment="OK",
            recommendations=[
                TradeRecommendation(
                    action="BUY",
                    symbol="LOWC",
                    confidence=0.5,
                    stop_loss_pct=0.15,
                    reasoning="Low conviction.",
                )
            ],
        )
        bear = TradingAnalysis(
            market_assessment="OK",
            recommendations=[
                TradeRecommendation(
                    action="BUY",
                    symbol="LOWC",
                    confidence=0.4,
                    stop_loss_pct=0.20,
                    reasoning="Very low conviction.",
                )
            ],
        )
        approved, disagreed = evaluate_consensus(bull, bear, min_confidence=0.6)
        assert len(approved) == 0

    def test_reject_when_different_symbols(self):
        """Models recommend different symbols -> no consensus."""
        from src.models import TradingAnalysis, TradeRecommendation
        from src.consensus import evaluate_consensus

        bull = TradingAnalysis(
            market_assessment="OK",
            recommendations=[
                TradeRecommendation(
                    action="BUY",
                    symbol="AAA",
                    confidence=0.9,
                    stop_loss_pct=0.15,
                    reasoning="Bull pick.",
                )
            ],
        )
        bear = TradingAnalysis(
            market_assessment="OK",
            recommendations=[
                TradeRecommendation(
                    action="BUY",
                    symbol="BBB",
                    confidence=0.9,
                    stop_loss_pct=0.15,
                    reasoning="Bear pick.",
                )
            ],
        )
        approved, disagreed = evaluate_consensus(bull, bear, min_confidence=0.6)
        assert len(approved) == 0
        assert "AAA" in disagreed
        assert "BBB" in disagreed

    def test_approved_trade_uses_min_confidence(
        self, sample_bull_analysis, sample_bear_analysis
    ):
        """Approved trade confidence = min(bull, bear)."""
        from src.consensus import evaluate_consensus

        approved, _ = evaluate_consensus(
            sample_bull_analysis, sample_bear_analysis, min_confidence=0.6
        )
        rxrx = [t for t in approved if t.symbol == "RXRX"]
        assert len(rxrx) == 1
        # Bull=0.8, Bear=0.7 -> min=0.7
        assert rxrx[0].confidence == 0.7

    def test_approved_trade_uses_max_stop_loss(
        self, sample_bull_analysis, sample_bear_analysis
    ):
        """Approved trade stop_loss_pct = max(bull, bear) (more conservative)."""
        from src.consensus import evaluate_consensus

        approved, _ = evaluate_consensus(
            sample_bull_analysis, sample_bear_analysis, min_confidence=0.6
        )
        rxrx = [t for t in approved if t.symbol == "RXRX"]
        assert len(rxrx) == 1
        # Bull=0.15, Bear=0.20 -> max=0.20
        assert rxrx[0].stop_loss_pct == 0.20


class TestQueryBull:
    """Test the OpenAI query function."""

    def test_query_bull_calls_openai_parse(self, mock_settings, sample_bull_analysis):
        """query_bull calls OpenAI parse() with correct model and response_format."""
        from unittest.mock import patch, MagicMock
        from src.consensus import query_bull

        mock_completion = MagicMock()
        mock_completion.choices[0].message.parsed = sample_bull_analysis

        with patch("src.consensus.OpenAI") as MockOpenAI:
            mock_client = MagicMock()
            MockOpenAI.return_value = mock_client
            mock_client.chat.completions.parse.return_value = mock_completion

            result = query_bull("test prompt")

            mock_client.chat.completions.parse.assert_called_once()
            call_kwargs = mock_client.chat.completions.parse.call_args[1]
            assert call_kwargs["model"] == "gpt-5.4-mini"
            assert call_kwargs["response_format"].__name__ == "TradingAnalysis"
            assert result == sample_bull_analysis


class TestQueryBear:
    """Test the Anthropic query function."""

    def test_query_bear_calls_anthropic_parse(
        self, mock_settings, sample_bear_analysis
    ):
        """query_bear calls Anthropic parse() with correct model and output_format."""
        from unittest.mock import patch, MagicMock
        from src.consensus import query_bear

        mock_response = MagicMock()
        mock_response.parsed_output = sample_bear_analysis

        with patch("src.consensus.anthropic") as mock_anthropic_mod:
            mock_client = MagicMock()
            mock_anthropic_mod.Anthropic.return_value = mock_client
            mock_client.messages.parse.return_value = mock_response

            result = query_bear("test prompt")

            mock_client.messages.parse.assert_called_once()
            call_kwargs = mock_client.messages.parse.call_args[1]
            assert call_kwargs["model"] == "claude-sonnet-4-6"
            assert call_kwargs["output_format"].__name__ == "TradingAnalysis"
            assert result == sample_bear_analysis


class TestRunConsensusCycle:
    """Test the full consensus cycle."""

    def test_aborts_on_bull_api_failure(self, mock_settings, sample_positions):
        """run_consensus_cycle raises on LLM API failure (no single-model fallback)."""
        from unittest.mock import patch, MagicMock
        from src.consensus import run_consensus_cycle

        with patch("src.consensus.query_bull", side_effect=Exception("OpenAI API down")):
            with pytest.raises(Exception, match="OpenAI API down"):
                run_consensus_cycle(
                    positions=sample_positions,
                    buying_power=5000.0,
                    candidates=["RXRX"],
                )

    def test_aborts_on_bear_api_failure(
        self, mock_settings, sample_positions, sample_bull_analysis
    ):
        """run_consensus_cycle raises on bear API failure (no fallback)."""
        from unittest.mock import patch
        from src.consensus import run_consensus_cycle

        with patch("src.consensus.query_bull", return_value=sample_bull_analysis):
            with patch(
                "src.consensus.query_bear",
                side_effect=Exception("Anthropic API down"),
            ):
                with pytest.raises(Exception, match="Anthropic API down"):
                    run_consensus_cycle(
                        positions=sample_positions,
                        buying_power=5000.0,
                        candidates=["RXRX"],
                    )

    def test_logs_to_llm_audit(
        self,
        mock_settings,
        test_db,
        sample_positions,
        sample_bull_analysis,
        sample_bear_analysis,
    ):
        """run_consensus_cycle logs both raw responses to llm_audit table."""
        from unittest.mock import patch, MagicMock
        from src.consensus import run_consensus_cycle

        with patch("src.consensus.query_bull", return_value=sample_bull_analysis):
            with patch("src.consensus.query_bear", return_value=sample_bear_analysis):
                with patch("src.consensus.get_db", return_value=test_db):
                    result = run_consensus_cycle(
                        positions=sample_positions,
                        buying_power=5000.0,
                        candidates=["RXRX", "ATOS"],
                    )

        rows = test_db.execute("SELECT * FROM llm_audit").fetchall()
        assert len(rows) == 2  # One for bull, one for bear

    def test_logs_to_consensus_decisions(
        self,
        mock_settings,
        test_db,
        sample_positions,
        sample_bull_analysis,
        sample_bear_analysis,
    ):
        """run_consensus_cycle logs consensus decision to consensus_decisions table."""
        from unittest.mock import patch
        from src.consensus import run_consensus_cycle

        with patch("src.consensus.query_bull", return_value=sample_bull_analysis):
            with patch("src.consensus.query_bear", return_value=sample_bear_analysis):
                with patch("src.consensus.get_db", return_value=test_db):
                    result = run_consensus_cycle(
                        positions=sample_positions,
                        buying_power=5000.0,
                        candidates=["RXRX", "ATOS"],
                    )

        rows = test_db.execute("SELECT * FROM consensus_decisions").fetchall()
        assert len(rows) == 1
        assert "RXRX" in rows[0]["agreed_tickers"]
