"""D. Walrus operator := (assign AND use in one expression)
Run with: uv run python -m python_features.walrus_demo"""
import re

calls = []  # records every call, so tests can count them


def expensive(n):
    calls.append(n)
    return n * n


# ---- conditional
def without_walrus(text):
    match = re.search(r"\d+", text)
    if match:
        return match.group()
    return None


def with_walrus(text):
    if match := re.search(r"\d+", text):
        return match.group()
    return None

# match = re.search(r"\d+", "order 42")
# if ( match ) :
#     print("match found:", match.group())


# ---- loop: read chunks until empty
def read_chunks_without(data, size=3):
    out, pos = [], 0
    chunk = data[pos:pos + size]
    while chunk:
        out.append(chunk)
        pos += size
        chunk = data[pos:pos + size]  # the same line written twice
    return out


def read_chunks_with(data, size=3):
    out, pos = [], 0
    while chunk := data[pos:pos + size]:  # written once
        out.append(chunk)
        pos += size
    return out


# ---- comprehension: call expensive() once per item instead of twice
def comp_without(nums):
    return [expensive(n) for n in nums if expensive(n) > 10]  # 2 calls per item


def comp_with(nums):
    return [y for n in nums if (y := expensive(n)) > 10]  # 1 call per item


if __name__ == "__main__":
    print(with_walrus("order 42"), without_walrus("none"))
    print(read_chunks_with([1, 2, 3, 4, 5, 6, 7]))
    calls.clear()
    print("without:", comp_without([2, 4]), "calls =", len(calls))
    calls.clear()
    print("with:   ", comp_with([2, 4]), "calls =", len(calls))
