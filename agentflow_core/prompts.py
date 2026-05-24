from __future__ import annotations

from string import Template

from agentflow_core.errors import ConfigError


def render_prompt(template: str, variables: dict[str, object]) -> str:
    try:
        return Template(template).substitute(
            {
                key: "" if value is None else str(value)
                for key, value in variables.items()
            }
        )
    except KeyError as exc:
        missing_key = exc.args[0]
        raise ConfigError(
            f"Prompt template is missing variable: {missing_key}"
        ) from exc
