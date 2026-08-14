"""PropertyCollector 페이징과 missingSet 처리 (계획 04 §4).

**토큰 반복 누락은 조용히 발생한다.** 첫 응답만 처리하면 `maxObjects` 초과분이
사라지는데 오류가 나지 않으므로 테스트로만 잡을 수 있다 (ROADMAP §13).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.infrastructure.vcenter.collector import PropertyCollectorReader, moref_id, props_to_dict


class _FakeMoRef:
    def __init__(self, moid: str) -> None:
        self._moId = moid


def _object_content(moid: str, props: dict[str, object], missing: list[str] | None = None):
    return SimpleNamespace(
        obj=_FakeMoRef(moid),
        propSet=[SimpleNamespace(name=k, val=v) for k, v in props.items()],
        missingSet=[SimpleNamespace(path=p) for p in (missing or [])],
    )


class _FakePropertyCollector:
    """maxObjects=2로 3페이지를 돌려주는 vCenter 흉내."""

    def __init__(self, pages: list[tuple[list, str | None]]) -> None:
        self._pages = pages
        self.retrieve_calls = 0
        self.continue_calls = 0
        self.seen_tokens: list[str] = []

    def RetrievePropertiesEx(self, specSet, options):  # noqa: N802 - pyVmomi 명명 규약
        self.retrieve_calls += 1
        objects, token = self._pages[0]
        return SimpleNamespace(objects=objects, token=token)

    def ContinueRetrievePropertiesEx(self, token):  # noqa: N802
        self.continue_calls += 1
        self.seen_tokens.append(token)
        index = next(i for i, (_, t) in enumerate(self._pages) if t == token) + 1
        objects, next_token = self._pages[index]
        return SimpleNamespace(objects=objects, token=next_token)


class _FakeSession:
    def __init__(self, collector: _FakePropertyCollector) -> None:
        self._collector = collector
        self.view_destroyed = False

    @property
    def content(self):  # type: ignore[no-untyped-def]
        session = self

        class _ViewManager:
            def CreateContainerView(self, container, type, recursive):  # noqa: N802, A002
                return _FakeView(session)

        return SimpleNamespace(
            propertyCollector=self._collector, viewManager=_ViewManager(), rootFolder=object()
        )


class _FakeView:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    def DestroyView(self) -> None:  # noqa: N802
        self._session.view_destroyed = True


@pytest.fixture(autouse=True)
def _stub_filter_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    """FilterSpec 구성을 건너뛴다.

    `PC.ObjectSpec(obj=...)`가 실제 ManagedObject를 요구하므로 목 뷰로는 만들 수 없다.
    여기서 검증하는 것은 **페이징 루프**이고, FilterSpec 구성은 Step 2에서 실제
    vCenter로 확인한다.
    """
    monkeypatch.setattr(
        "src.infrastructure.vcenter.collector.build_filter_spec",
        lambda view, obj_type, path_set: object(),
    )


@pytest.fixture
def paged_collector() -> _FakePropertyCollector:
    return _FakePropertyCollector(
        [
            ([_object_content("vm-1", {"name": "a"}), _object_content("vm-2", {"name": "b"})], "tok-1"),
            ([_object_content("vm-3", {"name": "c"}), _object_content("vm-4", {"name": "d"})], "tok-2"),
            ([_object_content("vm-5", {"name": "e"})], None),
        ]
    )


async def test_all_pages_are_retrieved(paged_collector: _FakePropertyCollector) -> None:
    """maxObjects를 초과하는 자원이 전량 수집되어야 한다."""
    session = _FakeSession(paged_collector)
    reader = PropertyCollectorReader(session, page_size=2)  # type: ignore[arg-type]

    results = [item async for item in reader.retrieve(object, ["name"])]

    assert [moid for moid, _ in results] == ["vm-1", "vm-2", "vm-3", "vm-4", "vm-5"]
    assert paged_collector.retrieve_calls == 1
    assert paged_collector.continue_calls == 2
    assert paged_collector.seen_tokens == ["tok-1", "tok-2"]


async def test_view_is_released_even_on_error() -> None:
    """DestroyView 누락 시 vCenter에 뷰 객체가 누적된다."""

    class _Failing(_FakePropertyCollector):
        def RetrievePropertiesEx(self, specSet, options):  # noqa: N802
            raise RuntimeError("boom")

    session = _FakeSession(_Failing([]))
    reader = PropertyCollectorReader(session, page_size=2)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError):
        [item async for item in reader.retrieve(object, ["name"])]

    assert session.view_destroyed is True


def test_missing_set_becomes_none_not_keyerror() -> None:
    """권한 부족·버전 미지원 속성은 missingSet으로 온다. 무시하면 KeyError가 난다."""
    content = _object_content(
        "vm-1", {"name": "web-01"}, missing=["config.createDate", "customValue"]
    )

    props = props_to_dict(content)

    assert props["name"] == "web-01"
    assert props["config.createDate"] is None
    assert props["customValue"] is None
    assert props.get("config.instanceUuid") is None  # 아예 없는 키도 안전하다


def test_empty_prop_and_missing_sets() -> None:
    content = SimpleNamespace(obj=_FakeMoRef("vm-9"), propSet=None, missingSet=None)
    assert props_to_dict(content) == {}
    assert moref_id(content.obj) == "vm-9"
