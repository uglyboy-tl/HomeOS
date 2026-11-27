"""pydantic 的报错压成一句人话：请求体校验与配置文件校验共用同一份翻译。"""

TEXT = {
    "missing": "必填",
    "int_parsing": "应该是整数",
    "int_type": "应该是整数",
    "float_parsing": "应该是数字",
    "bool_type": "应该是 true/false",
    "bool_parsing": "应该是 true/false",
    "string_type": "应该是字符串",
    "extra_forbidden": "不认识的键",
    "model_type": "应该是一段映射（key: value）",
}


def describe(error: dict, *, skip: int = 0) -> str:
    """`skip`：请求体的 loc 前面多一个 `body`/`query`，配置文件的没有。"""
    loc = [str(part) for part in error.get("loc", [])][skip:]
    where = ".".join(loc) or "参数"
    kind = str(error.get("type") or "")
    ctx = error.get("ctx") or {}
    if kind == "greater_than_equal":
        return f"{where}：不能小于 {ctx.get('ge')}"
    if kind == "less_than_equal":
        return f"{where}：不能大于 {ctx.get('le')}"
    raw = str(error.get("msg") or "格式不对")
    # pydantic 给 model_validator 的报错套了前缀
    raw = raw.removeprefix("Value error, ").removeprefix("Assertion failed, ")
    return f"{where}：{TEXT.get(kind) or raw}"
