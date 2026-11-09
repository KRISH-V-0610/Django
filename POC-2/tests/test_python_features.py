"""Topics A-E. These need no database."""
import subprocess
import sys
import uuid
from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from python_features import assert_demo, dataclass_demo, dataset_demo, walrus_demo
from python_features.uuid_demo import uuid7_timestamp

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ A. UUID
def test_uuid_versions():
    assert uuid.uuid4().version == 4
    assert uuid.uuid7().version == 7


def test_uuid7_sorts_by_creation_time_but_uuid4_does_not():
    v7s = [uuid.uuid7() for _ in range(200)]
    v4s = [uuid.uuid4() for _ in range(200)]
    assert v7s == sorted(v7s)
    assert v4s != sorted(v4s)  # 200 random values being sorted is practically impossible


def test_uuid7_contains_a_timestamp():
    before = datetime.now(timezone.utc) - timedelta(seconds=1)
    stamp = uuid7_timestamp(uuid.uuid7())
    assert before <= stamp <= datetime.now(timezone.utc) + timedelta(seconds=1)


# ---------------------------------------------------------- B. dataclasses
def test_normal_dataclass_can_change():
    p = dataclass_demo.NormalPoint(1, 2)
    p.x = 5
    assert p.x == 5


def test_frozen_dataclass_cannot_change():
    p = dataclass_demo.FrozenPoint(1, 2)
    with pytest.raises(FrozenInstanceError):
        p.x = 5


def test_default_factory_gives_each_instance_its_own_list():
    a, b = dataclass_demo.AppConfig("a"), dataclass_demo.AppConfig("b")
    a.allowed_hosts.append("x")
    assert b.allowed_hosts == []


def test_frozen_is_only_shallow():
    c = dataclass_demo.AppConfig("a")
    c.allowed_hosts.append("still works")  # the list inside is NOT frozen
    assert c.allowed_hosts == ["still works"]


def test_field_repr_false_hides_secret():
    c = dataclass_demo.AppConfig("a", secret="top-secret")
    assert "top-secret" not in repr(c)
    assert "secret" in [f.name for f in fields(c)]


def test_frozen_dataclass_is_hashable_normal_is_not():
    assert hash(dataclass_demo.FrozenPoint(1, 2)) == hash(dataclass_demo.FrozenPoint(1, 2))
    with pytest.raises(TypeError):
        hash(dataclass_demo.NormalPoint(1, 2))


# -------------------------------------------------------------- C. datasets
def test_clean_splits_good_and_bad_rows():
    good, bad = dataset_demo.clean(dataset_demo.ORDERS)
    assert [r["id"] for r in good] == [1, 2, 3]
    assert [r["id"] for r in bad] == [4, 5, 6]  # None, "oops", negative


def test_filter_transform_aggregate():
    good, _ = dataset_demo.clean(dataset_demo.ORDERS)
    assert [r["id"] for r in dataset_demo.filter_by_category(good, "tech")] == [2, 3]
    assert dataset_demo.add_tax(good)[0]["total"] == 295.0
    assert "total" not in good[0]  # original rows untouched
    assert dataset_demo.total_per_customer(good) == {"asha": 1050.0, "ravi": 1200.0}


def test_rows_to_columns():
    good, _ = dataset_demo.clean(dataset_demo.ORDERS)
    assert dataset_demo.to_columns(good)["amount"] == [250.0, 1200.0, 800.0]


# ---------------------------------------------------------------- D. walrus
def test_walrus_and_normal_give_same_results():
    assert walrus_demo.with_walrus("order 42") == walrus_demo.without_walrus("order 42") == "42"
    assert walrus_demo.with_walrus("none") is None
    data = [1, 2, 3, 4, 5, 6, 7]
    assert walrus_demo.read_chunks_with(data) == walrus_demo.read_chunks_without(data)


def test_walrus_in_comprehension_calls_function_fewer_times():
    walrus_demo.calls.clear()
    assert walrus_demo.comp_without([2, 4]) == [16]
    without_count = len(walrus_demo.calls)

    walrus_demo.calls.clear()
    assert walrus_demo.comp_with([2, 4]) == [16]
    with_count = len(walrus_demo.calls)

    assert (without_count, with_count) == (3, 2)


# ---------------------------------------------------------------- E. assert
def run_assert_demo(*python_flags):
    result = subprocess.run(
        [sys.executable, *python_flags, "-m", "python_features.assert_demo"],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    return result.stdout


def test_assert_raises_assertion_error_normally():
    with pytest.raises(AssertionError):
        assert_demo.withdraw_with_assert(100, 500)
    with pytest.raises(AssertionError):
        assert_demo.average_with_assert([])


def test_exception_always_protects_the_rule():
    with pytest.raises(ValueError):
        assert_demo.withdraw_with_exception(100, 500)


def test_python_O_flag_removes_asserts_but_not_exceptions():
    normal = run_assert_demo()
    optimized = run_assert_demo("-O")

    assert "asserts are ON" in normal
    assert "AssertionError: insufficient funds" in normal

    assert "asserts are OFF" in optimized
    assert "-400" in optimized  # the "protected" withdrawal went through!
    assert "ValueError: insufficient funds" in optimized  # real exception still works
