"""Runtime-only config interpretation; never rewrite HA's saved options."""


def default_true_option(options: dict, key: str) -> bool:
    """Match startup jq's null default without treating explicit false as absent."""
    value = options.get(key)
    return True if value is None else bool(value)
