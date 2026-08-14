"""PropertyCollector 페이징 조회 (계획 04 §4) — 이 어댑터의 핵심.

**자원을 하나씩 순회하며 개별 속성을 읽으면 안 된다** (D-007). 서버측 일괄 조회로
왕복을 줄인다.

주의 지점 두 가지:
- **`token` 반복이 필수다.** 첫 응답만 처리하면 `maxObjects` 초과분이 조용히 누락된다.
- **`missingSet` 처리.** 권한 부족·버전 미지원 속성은 `propSet`이 아니라 여기로 오며,
  무시하면 `KeyError`가 난다.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from pyVmomi import vim, vmodl

from src.infrastructure.vcenter.session import VCenterSession

logger = logging.getLogger(__name__)

PC = vmodl.query.PropertyCollector
DEFAULT_PAGE_SIZE = 500


def build_filter_spec(
    container_view: vim.view.ContainerView, obj_type: type, path_set: list[str]
) -> PC.FilterSpec:
    """ContainerView를 순회하며 지정 속성만 조회하는 FilterSpec을 만든다."""
    traversal = PC.TraversalSpec(
        name="traverseEntities",
        path="view",  # ContainerView.view 속성을 따라 순회
        skip=False,
        type=vim.view.ContainerView,
    )
    obj_spec = PC.ObjectSpec(
        obj=container_view,
        skip=True,  # 컨테이너 자체는 결과에서 제외
        selectSet=[traversal],
    )
    prop_spec = PC.PropertySpec(
        type=obj_type,
        all=False,  # 전체 속성 조회 금지 — 응답이 거대해진다
        pathSet=path_set,
    )
    return PC.FilterSpec(objectSet=[obj_spec], propSet=[prop_spec])


class PropertyCollectorReader:
    def __init__(self, session: VCenterSession, page_size: int = DEFAULT_PAGE_SIZE) -> None:
        self._session = session
        self._page_size = page_size

    def _create_view_sync(self, obj_type: type) -> vim.view.ContainerView:
        content = self._session.content
        return content.viewManager.CreateContainerView(
            container=content.rootFolder, type=[obj_type], recursive=True
        )

    def _release_view_sync(self, view: vim.view.ContainerView) -> None:
        """ContainerView 자원을 해제한다.

        `DestroyView`는 뷰 객체 정리이며 하이퍼바이저 자원 삭제가 아니다.
        메서드명 오해를 피하려고 래퍼 이름을 `_release_view_sync`로 둔다 (계획 04 §12).
        누락하면 vCenter에 뷰 객체가 누적되므로 `finally`로 보장한다.
        """
        try:
            view.DestroyView()
        except Exception:  # noqa: BLE001 - 정리 실패는 수집 결과를 바꾸지 않는다
            logger.debug("ContainerView 해제 실패 (무시)")

    def _retrieve_page_sync(
        self, filter_spec: PC.FilterSpec, token: str | None
    ) -> PC.RetrieveResult | None:
        pc = self._session.content.propertyCollector
        if token is None:
            options = PC.RetrieveOptions(maxObjects=self._page_size)
            return pc.RetrievePropertiesEx(specSet=[filter_spec], options=options)
        return pc.ContinueRetrievePropertiesEx(token=token)

    async def retrieve(
        self, obj_type: type, path_set: list[str]
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """(MoRef ID, 속성 dict) 튜플을 페이지 단위로 yield한다."""
        view = await asyncio.to_thread(self._create_view_sync, obj_type)
        try:
            filter_spec = build_filter_spec(view, obj_type, path_set)
            token: str | None = None
            while True:
                result = await asyncio.to_thread(self._retrieve_page_sync, filter_spec, token)
                if result is None:
                    break
                for obj_content in result.objects:
                    yield moref_id(obj_content.obj), props_to_dict(obj_content)
                token = getattr(result, "token", None)
                if not token:
                    break
        finally:
            await asyncio.to_thread(self._release_view_sync, view)


def moref_id(managed_object: Any) -> str:
    """Managed Object Reference를 문자열 ID로 변환한다. 예: 'vm-1234'"""
    return str(managed_object._moId)  # noqa: SLF001 - pyVmomi 공개 속성이 아니다


def props_to_dict(obj_content: Any) -> dict[str, Any]:
    """propSet을 dict로 변환하고 missingSet(조회 실패 속성)을 None으로 채운다."""
    props: dict[str, Any] = {p.name: p.val for p in (obj_content.propSet or [])}
    for miss in obj_content.missingSet or []:
        props[miss.path] = None  # 권한 부족·미지원 속성
    return props
