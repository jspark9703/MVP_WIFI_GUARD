"""
차트 렌더 헬퍼. lib/cache.py 와 함께 streamlit 을 import 하는 두 모듈 중 하나다
(plotting.py 는 순수 go.Figure 빌더로 유지된다).

존재 이유: plotly 의 도형 내부 제목(layout.title)은 상단 가로 legend 와 같은 띠를
차지해 **범례 라벨을 가린다.** 그래서 빌더는 제목을 layout.meta["title"] 로만 실어
보내고, 여기서 차트 **위 마크다운 텍스트**로 그린다. 결과적으로 제목은 항상 읽히고
legend 는 가려지지 않는다.
"""

from typing import Any, Optional

import plotly.graph_objects as go
import streamlit as st


def figure_title(fig: go.Figure) -> Optional[str]:
    """빌더가 layout.meta 에 실어 보낸 제목을 꺼낸다."""
    meta = getattr(fig.layout, "meta", None)
    if isinstance(meta, dict):
        title = meta.get("title")
        return str(title) if title else None
    return None


def chart(fig: go.Figure, caption: Optional[str] = None,
          title: Optional[str] = None, **kwargs: Any) -> None:
    """
    제목(차트 위 텍스트) + 차트 + 선택적 캡션을 함께 렌더한다.

    Args:
        title: 명시하면 layout.meta 의 제목을 덮어쓴다. 빈 문자열이면 제목을 숨긴다.
        caption: 차트 아래 설명.
        kwargs: st.plotly_chart 로 그대로 전달. 설치된 Streamlit 1.44.1 에서는
            width="stretch" 가 동작하지 않으므로 use_container_width 를 기본값으로 쓴다.
    """
    label = figure_title(fig) if title is None else title
    if label:
        st.markdown(f"**{label}**")

    kwargs.pop("width", None)  # 1.44.1 에서는 무시되는 인자라 조용히 걷어낸다
    kwargs.setdefault("use_container_width", True)
    st.plotly_chart(fig, **kwargs)

    if caption:
        st.caption(caption)
