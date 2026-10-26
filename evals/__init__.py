_EXPORTS = {
    "run_pipeline": "evals.pipeline",
    "load_golden_dataset": "evals.pipeline",
    "run_guardrails_eval": "evals.guardrails_eval",
    "compute_guardrails_metrics": "evals.guardrails_eval",
    "run_all_metrics": "evals.metrics",
}


def __getattr__(name):
    if name in _EXPORTS:
        import importlib

        return getattr(importlib.import_module(_EXPORTS[name]), name)
    raise AttributeError(name)


__all__ = list(_EXPORTS)
