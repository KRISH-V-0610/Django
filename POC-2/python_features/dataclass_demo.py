"""B. Frozen dataclasses - run with: uv run python -m python_features.dataclass_demo"""
from dataclasses import FrozenInstanceError, dataclass, field


@dataclass
class NormalPoint:  # can be changed after creation
    x: int
    y: int


@dataclass(frozen=True)
class FrozenPoint:  # cannot be changed after creation
    x: int
    y: int
    z: int


@dataclass(frozen=True)
class AppConfig:
    """Typical use: settings / DTO that should never change after creation."""

    app_name: str
    debug: bool = False
    # a list must NOT be a plain default (it would be shared) -> default_factory
    allowed_hosts: list[str] = field(default_factory=list)
    # field(...) can also hide a value from repr
    secret: str = field(default="", repr=False)


if __name__ == "__main__":
    n = NormalPoint(1, 2)
    n.x = 99
    print("normal after change:", n)

    f = FrozenPoint(1, 2)
    try:
        f.x = 99
    except FrozenInstanceError as e:
        print("frozen change ->", type(e).__name__, e)

    c = AppConfig("shop", allowed_hosts=["localhost"], secret="abc")
    print(c)  # secret is hidden
    print("two configs equal?", AppConfig("a") == AppConfig("a"))  # dataclass gives __eq__
    print("hashable (frozen)?", hash(f) is not None)

    # LIMITATION: frozen is shallow. The list inside can still be changed.
    c.allowed_hosts.append("evil.com")
    print("shallow only:", c.allowed_hosts)
