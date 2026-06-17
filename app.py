# -*- coding: utf-8 -*-
"""
국내/국제 주요 뉴스 현황판 (Streamlit)
- 데이터 소스: 구글 뉴스 RSS (API 키 불필요)
- 카테고리: 국내경제 / 국제경제 / 정치 / 사회 / 안보·외교
- 표 포맷: 헤드라인 / 언론사 / 주요내용 / 기사시간 / 링크
"""

import re
import html
import urllib.parse
from datetime import datetime, timezone, timedelta

import feedparser
import pandas as pd
import streamlit as st

KST = timezone(timedelta(hours=9))

# ─────────────────────────────────────────────────────────────
# 카테고리별 구글 뉴스 검색어 (필요하면 자유롭게 수정/추가)
#   OR 로 키워드를 묶으면 합집합으로 검색됨
# ─────────────────────────────────────────────────────────────
CATEGORIES = {
    "국내경제": "한국 경제 OR 금리 OR 물가 OR 코스피 OR 환율 OR 부동산",
    "국제경제": "글로벌 경제 OR 연준 OR 미국 증시 OR 국제 유가 OR 무역",
    "정치":     "정치 OR 국회 OR 대통령 OR 여야",
    "사회":     "사회 OR 사건사고 OR 노동 OR 교육",
    "안보·외교": "외교 OR 안보 OR 국방 OR 북한 OR 한미 OR 한중",
}

st.set_page_config(page_title="뉴스 현황판", page_icon="📰", layout="wide")


# ─────────────────────────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────────────────────────
def strip_html(raw: str) -> str:
    """RSS description 에 섞인 HTML 태그/엔티티 제거."""
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)          # 태그 제거
    text = html.unescape(text)                   # &amp; 등 복원
    text = re.sub(r"\s+", " ", text).strip()     # 공백 정리
    return text


def parse_source_and_title(entry):
    """언론사명과 깨끗한 제목을 분리."""
    title = entry.get("title", "").strip()
    source = ""
    src = entry.get("source")
    if src:
        # feedparser 에서 source 는 dict-like
        source = (src.get("title") if hasattr(src, "get") else getattr(src, "title", "")) or ""
    # 제목이 "기사제목 - 언론사" 형태면 끝부분에서 언론사 추출
    if " - " in title:
        head, tail = title.rsplit(" - ", 1)
        if not source:
            source = tail.strip()
        title = head.strip()
    return title, (source or "기타")


def to_kst(entry):
    """published_parsed(UTC struct_time) → KST datetime."""
    tm = entry.get("published_parsed") or entry.get("updated_parsed")
    if not tm:
        return None
    dt_utc = datetime(*tm[:6], tzinfo=timezone.utc)
    return dt_utc.astimezone(KST)


@st.cache_data(ttl=600, show_spinner=False)
def fetch_news(query: str, limit: int = 25) -> pd.DataFrame:
    """구글 뉴스 RSS 검색 → DataFrame 반환."""
    q = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
    feed = feedparser.parse(url)

    rows = []
    for e in feed.entries[:limit]:
        title, source = parse_source_and_title(e)
        summary = strip_html(e.get("summary", ""))
        # 구글 뉴스 요약은 종종 제목 반복 → 같으면 비움
        if summary[:30] == title[:30]:
            summary = ""
        rows.append({
            "헤드라인": title,
            "언론사": source,
            "주요내용": summary,
            "기사시간": to_kst(e),
            "링크": e.get("link", ""),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("기사시간", ascending=False, na_position="last").reset_index(drop=True)
    return df


# ─────────────────────────────────────────────────────────────
# 사이드바 (필터)
# ─────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ 설정")
    selected = st.multiselect(
        "카테고리 선택",
        options=list(CATEGORIES.keys()),
        default=list(CATEGORIES.keys()),
    )
    per_cat = st.slider("카테고리당 기사 수", 5, 30, 15, step=5)
    hours = st.slider("최근 N시간 이내", 1, 72, 24, step=1)
    keyword = st.text_input("키워드 필터 (제목/내용 검색)", "")
    if st.button("🔄 새로고침 (캐시 비우기)"):
        st.cache_data.clear()
        st.rerun()
    st.caption("데이터: 구글 뉴스 RSS · 10분 캐시")


# ─────────────────────────────────────────────────────────────
# 본문
# ─────────────────────────────────────────────────────────────
st.title("📰 국내·국제 주요 뉴스 현황판")
st.caption(f"마지막 갱신: {datetime.now(KST):%Y-%m-%d %H:%M:%S} KST")

COLUMN_CONFIG = {
    "헤드라인": st.column_config.TextColumn("헤드라인", width="large"),
    "언론사":   st.column_config.TextColumn("언론사", width="small"),
    "주요내용": st.column_config.TextColumn("주요내용", width="large"),
    "기사시간": st.column_config.DatetimeColumn("기사시간", format="MM-DD HH:mm", width="small"),
    "링크":     st.column_config.LinkColumn("링크", display_text="원문 →", width="small"),
}

cutoff = datetime.now(KST) - timedelta(hours=hours)


def render_table(df: pd.DataFrame):
    if df.empty:
        st.info("조건에 맞는 기사가 없어.")
        return
    view = df.copy()
    # 시간 필터
    view = view[view["기사시간"].notna() & (view["기사시간"] >= cutoff)]
    # 키워드 필터
    if keyword.strip():
        kw = keyword.strip()
        mask = view["헤드라인"].str.contains(kw, case=False, na=False) | \
               view["주요내용"].str.contains(kw, case=False, na=False)
        view = view[mask]
    if view.empty:
        st.info("조건에 맞는 기사가 없어.")
        return
    st.dataframe(
        view,
        column_config=COLUMN_CONFIG,
        hide_index=True,
        use_container_width=True,
    )
    st.caption(f"표시 {len(view)}건")


if not selected:
    st.warning("왼쪽에서 카테고리를 하나 이상 선택해줘.")
else:
    tabs = st.tabs(selected)
    for tab, cat in zip(tabs, selected):
        with tab:
            with st.spinner(f"{cat} 불러오는 중..."):
                df = fetch_news(CATEGORIES[cat], limit=per_cat)
            render_table(df)
