"""E. assert vs exceptions
Normal run:  uv run python -m python_features.assert_demo
With -O:     uv run python -O -m python_features.assert_demo   (asserts are REMOVED)"""


def average_with_assert(numbers):
    assert len(numbers) > 0, "numbers must not be empty"  # developer sanity check
    return sum(numbers) / len(numbers)


def withdraw_with_assert(balance, amount):
    # BAD: a business rule hidden in assert. With -O this check disappears!
    assert amount <= balance, "insufficient funds"
    return balance - amount


def withdraw_with_exception(balance, amount):
    # GOOD: real rule + untrusted input -> always a real exception
    if amount > balance:
        raise ValueError("insufficient funds")
    return balance - amount


if __name__ == "__main__":
    try:
        assert False
        print("asserts are OFF (running with -O)")
    except AssertionError:
        print("asserts are ON")

    print("withdraw 500 from 100 using assert    ->", end=" ")
    try:
        print(withdraw_with_assert(100, 500))  # with -O this prints -400 (!)
    except AssertionError as e:
        print("AssertionError:", e)

    print("withdraw 500 from 100 using exception ->", end=" ")
    try:
        print(withdraw_with_exception(100, 500))
    except ValueError as e:
        print("ValueError:", e)
