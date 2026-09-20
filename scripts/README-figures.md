# Figures rendered by Quarto

The paper can generate benchmark figures as part of the normal `quarto render`
process. This keeps figures reproducible and tied to the underlying data instead
of storing manually edited chart images.

## Setup

Install the Python dependencies once:

```bash
python -m pip install -r requirements.txt
```

Then render normally:

```bash
quarto render pa1.qmd
```

`pa1.qmd` selects the `python3` Jupyter kernel, so executable Python cells in
included chapter files run during rendering. Quarto captures the Matplotlib
figure and inserts it into the Typst/PDF output as a numbered figure.

## Pareto frontier pattern

Once the frozen benchmark results exist, a results chapter can contain a cell
like this:

````qmd
```{python}
#| label: fig-cost-success-pareto
#| fig-cap: "Cost-performance Pareto frontier across evaluated configurations."

import pandas as pd

results = pd.read_csv("data/benchmark-results.csv")
plot_pareto_frontier(
    results,
    cost_col="cost_per_task_usd",
    performance_col="success_rate",
    label_col="configuration",
)
```
````

Lower cost and higher success are treated as better by
`plot_pareto_frontier`. The helper draws the non-dominated frontier and leaves
the underlying observations visible.

## Line-chart pattern

For comparisons where models have a meaningful order, such as increasing model
cost, keep that order explicit:

````qmd
```{python}
#| label: fig-harness-success-by-model
#| fig-cap: "Task success by harness across models ordered by model cost."

model_order = ["Model A", "Model B", "Model C"]

plot_grouped_line(
    results,
    x_col="model",
    y_col="success_rate",
    group_col="harness",
    x_order=model_order,
    x_label="Model",
    y_label="Task success rate",
)
```
````

Do not replace research values with example values in the paper. Keep raw or
frozen result data under `data/`, and keep plotting code reproducible in the
Quarto source or `scripts/`.
