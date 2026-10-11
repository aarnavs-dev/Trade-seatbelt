"""Money is always an int in minor units (cents). This is the only place it is formatted."""


def fmt(minor: int) -> str:
    sign = "-" if minor < 0 else ""
    n = abs(minor)
    return f"{sign}${n // 100:,}.{n % 100:02d}"
