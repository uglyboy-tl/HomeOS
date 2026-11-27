"""假米家：只实现服务层真正用到的那几个方法（get/set/run_action/get_devices_list）。

能模拟云端的坏脾气：读回延迟、一直不生效、读抛异常、没登录、库没装。
"""

from __future__ import annotations


class FakeDevice:
    def __init__(self, api: FakeApi, did: str) -> None:
        self._api = api
        self.did = did

    def get(self, prop: str):
        api = self._api
        api.gets.append((self.did, prop))
        if api.get_error is not None:
            raise api.get_error
        pending = api.pending.get(self.did)
        if pending is not None:
            remaining, value = pending
            if remaining > 0:
                api.pending[self.did] = (remaining - 1, value)
            else:
                api.props.setdefault(self.did, {})[prop] = value
                api.pending.pop(self.did, None)
        return api.props.get(self.did, {}).get(prop)

    def set(self, prop: str, value) -> None:
        api = self._api
        api.sets.append((self.did, prop, value))
        if api.set_error is not None:
            raise api.set_error
        api.pending[self.did] = (api.reads_before_apply, value)
        if api.reads_before_apply == 0:
            api.props.setdefault(self.did, {})[prop] = value

    def run_action(self, action: str, **kwargs) -> None:
        self._api.actions.append((self.did, action, kwargs))


class FakeApi:
    """行为开关都在构造参数里，默认是一台"立刻生效、不出错"的云。"""

    def __init__(
        self,
        props: dict | None = None,
        devices: list | None = None,
        reads_before_apply: int = 0,
        get_error: Exception | None = None,
        set_error: Exception | None = None,
    ) -> None:
        self.props = {did: dict(values) for did, values in (props or {}).items()}
        self.device_list = list(devices or [])
        self.device_list_calls = 0
        self.reads_before_apply = reads_before_apply
        self.get_error = get_error
        self.set_error = set_error
        self.gets: list[tuple[str, str]] = []
        self.sets: list[tuple[str, str, object]] = []
        self.actions: list[tuple[str, str, dict]] = []
        self.pending: dict[str, tuple[int, object]] = {}

    def get_devices_list(self) -> list:
        self.device_list_calls += 1
        return self.device_list


def install(monkeypatch, service, api: FakeApi, *, login_error: Exception | None = None):
    """把假云装进 mijia 服务：替换构造器、设备构造器与登录态文件。"""

    def make_api():
        if login_error is not None:
            raise login_error
        return api

    monkeypatch.setattr(service, "mijiaAPI", make_api)
    monkeypatch.setattr(service, "mijiaDevice", lambda _api, did, **_kw: FakeDevice(api, did))
    return api
