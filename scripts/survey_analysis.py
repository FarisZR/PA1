"""Derived views for the internal AI coding-agent survey.

The raw survey remains in ``data/company-survey.csv``. This module normalizes
answer labels and derives the respondent- and configuration-level tables used by
the current-state figures.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pandas as pd

HARNESS_COLUMNS = [
    "Your First choice Agent App / Agent Harness",
    "Agent / Harness (Optional)",
    "Agent / Harness",
    "Agent / Harness.1",
]
MODEL_COLUMNS = ["Model", "Model  (Optional)", "Model.1", "Model.2"]
UNKNOWN_MODELS = {"Other", "I don't know / automatically selected"}

HARNESS_RENAMES = {
    "Claude (Code/Desktop code mode)": "Claude Code",
    "Claude (Code CLI/Desktop code mode)": "Claude Code",
    "Codex (app/cli)": "Codex",
    "GitHub Copilot (CLI/VSCode)": "GitHub Copilot",
}

MODEL_PROVIDERS = {
    "Claude": "Anthropic",
    "GPT-": "OpenAI",
    "Gemini": "Google",
    "DeepSeek": "DeepSeek",
    "Kimi": "Moonshot",
    "MiniMax": "MiniMax",
}


def load_survey(path: str | Path = "data/company-survey.csv") -> pd.DataFrame:
    return pd.read_csv(path)


def load_model_tiers(path: str | Path = "data/model-cost-tiers.csv") -> pd.DataFrame:
    tiers = pd.read_csv(path)
    tiers["deep_swe_avg_cost_usd"] = pd.to_numeric(
        tiers["deep_swe_avg_cost_usd"], errors="coerce"
    )
    return tiers


def normalize_harness(value: object) -> str | None:
    if pd.isna(value):
        return None
    text = str(value).strip()
    return HARNESS_RENAMES.get(text, text)


def model_provider(value: object) -> str | None:
    if pd.isna(value):
        return None
    model = str(value).strip()
    if model in UNKNOWN_MODELS:
        return None
    for prefix, provider in MODEL_PROVIDERS.items():
        if model.startswith(prefix):
            return provider
    return None


def iter_configurations(survey: pd.DataFrame):
    """Yield unique respondent/harness/model pairs with their survey slot.

    Repeated identical pairs within one response are emitted once. Slot zero is
    the first-choice configuration; later slots are secondary configurations.
    """
    for respondent, row in survey.iterrows():
        seen: set[tuple[str | None, str | None]] = set()
        for slot, (harness_col, model_col) in enumerate(
            zip(HARNESS_COLUMNS, MODEL_COLUMNS, strict=True)
        ):
            harness = normalize_harness(row[harness_col])
            model = None if pd.isna(row[model_col]) else str(row[model_col]).strip()
            if harness is None and model is None:
                continue
            pair = (harness, model)
            if pair in seen:
                continue
            seen.add(pair)
            yield {
                "respondent": respondent,
                "slot": slot,
                "role": "Primary" if slot == 0 else "Secondary",
                "harness": harness,
                "model": model,
            }


def harness_usage(survey: pd.DataFrame) -> pd.DataFrame:
    n = len(survey)
    primary = survey[HARNESS_COLUMNS[0]].map(normalize_harness).value_counts()

    weekly_sets = []
    for _, row in survey.iterrows():
        weekly_sets.append(
            {
                normalize_harness(row[col])
                for col in HARNESS_COLUMNS
                if normalize_harness(row[col]) is not None
            }
        )
    weekly = pd.Series(
        [harness for values in weekly_sets for harness in values], dtype="object"
    ).value_counts()

    order = list(weekly.index)
    frame = pd.DataFrame({"harness": order})
    frame["First choice"] = frame["harness"].map(primary).fillna(0).astype(int)
    frame["Any weekly use"] = frame["harness"].map(weekly).fillna(0).astype(int)
    frame["First choice %"] = frame["First choice"] / n * 100
    frame["Any weekly use %"] = frame["Any weekly use"] / n * 100
    return frame.sort_values(["Any weekly use", "First choice", "harness"], ascending=[False, False, True]).reset_index(drop=True)


def model_usage(survey: pd.DataFrame) -> pd.DataFrame:
    n = len(survey)
    primary_values = survey[MODEL_COLUMNS[0]].dropna().astype(str)
    primary_values = primary_values[~primary_values.isin(UNKNOWN_MODELS)]
    primary = primary_values.value_counts()

    weekly_sets = []
    for _, row in survey.iterrows():
        models = {
            str(row[col]).strip()
            for col in MODEL_COLUMNS
            if not pd.isna(row[col]) and str(row[col]).strip() not in UNKNOWN_MODELS
        }
        weekly_sets.append(models)
    weekly = pd.Series(
        [model for values in weekly_sets for model in values], dtype="object"
    ).value_counts()

    order = list(weekly.index)
    frame = pd.DataFrame({"model": order})
    frame["Primary"] = frame["model"].map(primary).fillna(0).astype(int)
    frame["Any weekly use"] = frame["model"].map(weekly).fillna(0).astype(int)
    frame["Primary %"] = frame["Primary"] / n * 100
    frame["Any weekly use %"] = frame["Any weekly use"] / n * 100
    return frame.sort_values(["Any weekly use", "Primary", "model"], ascending=[False, False, True]).reset_index(drop=True)


def _configuration_frame(survey: pd.DataFrame, tiers: pd.DataFrame) -> pd.DataFrame:
    configs = pd.DataFrame(iter_configurations(survey))
    return configs.merge(tiers[["model", "tier"]], on="model", how="left")


def harness_tier_distribution(
    survey: pd.DataFrame,
    tiers: pd.DataFrame,
    harnesses: Iterable[str] | None = None,
) -> pd.DataFrame:
    configs = _configuration_frame(survey, tiers).dropna(subset=["harness", "tier"])
    counts = pd.crosstab(configs["harness"], configs["tier"])
    for tier in ["High cost", "Medium cost", "Low cost"]:
        if tier not in counts.columns:
            counts[tier] = 0
    counts = counts[["High cost", "Medium cost", "Low cost"]]
    if harnesses is not None:
        order = [h for h in harnesses if h in counts.index]
        counts = counts.loc[order]
    shares = counts.div(counts.sum(axis=1), axis=0) * 100
    totals = counts.sum(axis=1)
    shares.index.name = "harness"
    result = shares.reset_index()
    result["harness_label"] = [f"{harness} (n={int(totals.loc[harness])})" for harness in result["harness"]]
    return result


def role_tier_distribution(survey: pd.DataFrame, tiers: pd.DataFrame) -> pd.DataFrame:
    configs = _configuration_frame(survey, tiers).dropna(subset=["tier"])
    counts = pd.crosstab(configs["role"], configs["tier"])
    for tier in ["High cost", "Medium cost", "Low cost"]:
        if tier not in counts.columns:
            counts[tier] = 0
    counts = counts[["High cost", "Medium cost", "Low cost"]]
    counts = counts.reindex(["Primary", "Secondary"])
    shares = counts.div(counts.sum(axis=1), axis=0) * 100
    totals = counts.sum(axis=1)
    shares.index.name = "role"
    result = shares.reset_index()
    result["role_label"] = [f"{role} (n={int(totals.loc[role])})" for role in result["role"]]
    return result


def provider_diversification(survey: pd.DataFrame) -> pd.DataFrame:
    categories = {
        "Anthropic only": 0,
        "Anthropic + alternatives": 0,
        "Alternatives only": 0,
        "Unclassified": 0,
    }
    for _, row in survey.iterrows():
        providers = {
            model_provider(row[col])
            for col in MODEL_COLUMNS
            if model_provider(row[col]) is not None
        }
        has_anthropic = "Anthropic" in providers
        has_alternative = any(provider != "Anthropic" for provider in providers)
        if has_anthropic and has_alternative:
            categories["Anthropic + alternatives"] += 1
        elif has_anthropic:
            categories["Anthropic only"] += 1
        elif has_alternative:
            categories["Alternatives only"] += 1
        else:
            categories["Unclassified"] += 1
    n = len(survey)
    frame = pd.DataFrame(
        [{"category": category, "respondents": count} for category, count in categories.items()]
    )
    frame["percentage"] = frame["respondents"] / n * 100
    return frame


def respondent_profiles(survey: pd.DataFrame, tiers: pd.DataFrame) -> list[dict]:
    tier_by_model = tiers.set_index("model")["tier"].to_dict()
    profiles = []
    for respondent, row in survey.iterrows():
        harnesses = {
            normalize_harness(row[col])
            for col in HARNESS_COLUMNS
            if normalize_harness(row[col]) is not None
        }
        models = {
            str(row[col]).strip()
            for col in MODEL_COLUMNS
            if not pd.isna(row[col]) and str(row[col]).strip() not in UNKNOWN_MODELS
        }
        providers = {model_provider(model) for model in models if model_provider(model)}
        model_tiers = {tier_by_model.get(model) for model in models if tier_by_model.get(model)}
        profiles.append(
            {
                "respondent": respondent,
                "harnesses": harnesses,
                "models": models,
                "providers": providers,
                "tiers": model_tiers,
            }
        )
    return profiles
