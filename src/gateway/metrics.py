"""
Observability — Prometheus-compatible /metrics endpoint and in-memory counters.

Exposes request latency, LLM call stats, tool execution times, and failover rates.
No external dependencies; outputs Prometheus text exposition format.
"""

import threading
import logging
from typing import Dict, List
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class _Metric:
    name: str
    help_text: str
    type_: str = "counter"
    labels: Dict[str, str] = field(default_factory=dict)
    _value: float = 0.0
    _observations: List[float] = field(default_factory=list)

    def inc(self, amount: float = 1.0):
        self._value += amount

    def observe(self, value: float):
        self._observations.append(value)
        # Keep last 10000 observations for histogram calculation
        if len(self._observations) > 10000:
            self._observations = self._observations[-5000:]

    @property
    def value(self) -> float:
        return self._value

    def percentile(self, p: float) -> float:
        if not self._observations:
            return 0.0
        sorted_obs = sorted(self._observations)
        idx = int(len(sorted_obs) * p / 100.0)
        idx = min(idx, len(sorted_obs) - 1)
        return sorted_obs[idx]


class MetricsRegistry:
    """Thread-safe in-memory metrics registry with Prometheus text output."""

    def __init__(self):
        self._metrics: Dict[str, _Metric] = {}
        self._lock = threading.Lock()

    def counter(self, name: str, help_text: str = "") -> _Metric:
        with self._lock:
            if name not in self._metrics:
                self._metrics[name] = _Metric(name=name, help_text=help_text, type_="counter")
            return self._metrics[name]

    def histogram(self, name: str, help_text: str = "") -> _Metric:
        with self._lock:
            if name not in self._metrics:
                self._metrics[name] = _Metric(name=name, help_text=help_text, type_="histogram")
            return self._metrics[name]

    def gauge(self, name: str, help_text: str = "") -> _Metric:
        with self._lock:
            if name not in self._metrics:
                self._metrics[name] = _Metric(name=name, help_text=help_text, type_="gauge")
            return self._metrics[name]

    def set_gauge(self, name: str, value: float):
        m = self.gauge(name)
        with self._lock:
            m._value = value

    def expose(self) -> str:
        """Generate Prometheus text exposition format."""
        lines = []
        with self._lock:
            for m in self._metrics.values():
                lines.append(f"# HELP {m.name} {m.help_text}")
                lines.append(f"# TYPE {m.name} {m.type_}")
                if m.type_ == "histogram":
                    lines.append(f"{m.name}_count {len(m._observations)}")
                    lines.append(f"{m.name}_sum {sum(m._observations):.4f}")
                    for p in (50, 95, 99):
                        lines.append(f'{m.name}{{quantile="{p / 100}"}} {m.percentile(p):.4f}')
                else:
                    lines.append(f"{m.name} {m._value:.4f}")
        return "\n".join(lines) + "\n"

    def expose_dict(self) -> Dict[str, Dict]:
        """Return all metrics as a JSON-friendly dict for dashboard consumption."""
        result: Dict[str, Dict] = {}
        with self._lock:
            for m in self._metrics.values():
                entry: Dict = {"type": m.type_, "help": m.help_text}
                if m.type_ == "histogram":
                    entry["count"] = len(m._observations)
                    entry["sum"] = round(sum(m._observations), 4)
                    entry["p50"] = round(m.percentile(50), 4)
                    entry["p95"] = round(m.percentile(95), 4)
                    entry["p99"] = round(m.percentile(99), 4)
                else:
                    entry["value"] = round(m._value, 4)
                result[m.name] = entry
        return result

    def observe_tokens(self, provider: str, prompt_tokens: int, completion_tokens: int, model: str = ""):
        "Record token usage for a provider/model combination."
        c = self.counter("llm_tokens_prompt_total", "Total prompt tokens across all providers")
        c.inc(prompt_tokens)
        c2 = self.counter("llm_tokens_completion_total", "Total completion tokens across all providers")
        c2.inc(completion_tokens)
        # Per-provider tracking
        provider_prompt = self.counter(f"llm_tokens_prompt_{provider}", f"Prompt tokens for {provider}")
        provider_prompt.inc(prompt_tokens)
        provider_completion = self.counter(f"llm_tokens_completion_{provider}", f"Completion tokens for {provider}")
        provider_completion.inc(completion_tokens)
        if model:
            model_total = self.counter(f"llm_tokens_{provider}_{model}", f"Total tokens for {provider}/{model}")
            model_total.inc(prompt_tokens + completion_tokens)

    def observe_tool_execution(self, tool_name: str, duration_s: float, status: str = "success"):
        """Record tool execution latency and outcome."""
        self.histogram("tool_execution_seconds", "Tool execution latency in seconds").observe(duration_s)
        self.histogram(
            f"tool_execution_{tool_name}_seconds",
            f"Latency for tool {tool_name} in seconds",
        ).observe(duration_s)
        self.counter("tool_execution_total", "Total tool executions").inc()
        self.counter(f"tool_execution_{status}_total", f"Total tool executions with status={status}").inc()
        self.counter(
            f"tool_execution_{tool_name}_{status}_total",
            f"Total executions for tool {tool_name} with status={status}",
        ).inc()

    def observe_subtask_execution(self, duration_s: float, status: str = "success"):
        """Record subtask execution latency and outcome."""
        self.histogram("subtask_execution_seconds", "Subtask execution latency in seconds").observe(duration_s)
        self.counter("subtask_execution_total", "Total subtask executions").inc()
        self.counter(f"subtask_execution_{status}_total", f"Total subtask executions with status={status}").inc()

    def observe_failover(self, from_provider: str, to_provider: str):
        """Record provider failover transitions."""
        self.counter("llm_failover_total", "Total LLM provider failovers").inc()
        self.counter(
            f"llm_failover_{from_provider}_to_{to_provider}_total",
            f"Total failovers from {from_provider} to {to_provider}",
        ).inc()

    def observe_llm_error(self, provider: str):
        """Record provider-side LLM call errors."""
        self.counter("llm_errors_total", "Total LLM call errors").inc()
        self.counter(f"llm_errors_{provider}_total", f"Total LLM call errors for provider {provider}").inc()

    # V15: L1 硬路由 + Strategist 语义主权 metrics
    def observe_v15_l1_gate(self, result: str, duration_s: float):
        """Record L1 hard-rule gate hit."""
        self.counter("route_l1_gate_total", "L1 gate total hits").inc()
        self.counter(f"route_l1_gate_{result}_total", f"L1 gate {result}").inc()
        self.histogram("route_l1_gate_seconds", "L1 gate latency").observe(duration_s)

    def observe_v15_plan_decision(self, mode: str, model_tier: str):
        """Record Strategist.decide() output distribution."""
        self.counter("plan_decision_total", "PlanDecision total").inc()
        self.counter(f"plan_decision_{mode}_total", f"PlanDecision mode={mode}").inc()
        self.counter(f"model_tier_{model_tier}_selected_total", f"Model tier {model_tier} selected").inc()

    def observe_v15_skill_hint(self, skill: str):
        """Record SkillIndex hint hit."""
        self.counter("route_skill_hint_total", "SkillIndex hint total").inc()
        self.counter(f"route_skill_hint_{skill}_total", f"SkillIndex hint: {skill}").inc()


# Global singleton
metrics = MetricsRegistry()
