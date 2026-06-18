# -*- coding: utf-8 -*-
"""
국내/국제 주요 뉴스 현황판 (Streamlit) v2
- 데이터 소스: 구글 뉴스 RSS (국내 ko + 외신 en)
- 6개 대분류, 국내/외신 혼합, 중복기사 묶기, 키워드 도넛차트
"""

import re
import html
import urllib.parse
from collections import Counter
from difflib import SequenceMatcher
from datetime import datetime, timezone, timedelta

import feedparser
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

KST = timezone(timedelta(hours=9))

# ─────────────────────────────────────────────────────────────
# 카테고리 설정
#   ko  : 국내(한국어) 검색어 / en : 외신(영어) 검색어
#   n_ko: 국내 기사 수    / n_en: 외신 기사 수
#   ↓ 숫자만 바꾸면 개수 조정됨
# ─────────────────────────────────────────────────────────────
CATEGORY_CONFIG = {
    "국내경제": {
        "ko": "한국 경제 OR 금리 OR 물가 OR 코스피 OR 환율 OR 부동산 OR 수출",
        "en": "South Korea economy OR Korean won OR Bank of Korea OR Korea inflation",
        "n_ko": 10, "n_en": 5,
    },
    "국제경제": {
        "ko": "글로벌 경제 OR 연준 OR 미국 증시 OR 국제 유가 OR 무역 OR 관세",
        "en": "global economy OR Federal Reserve OR stock market OR oil prices OR trade war",
        "n_ko": 10, "n_en": 5,
    },
    "정치": {
        "ko": "정치 OR 국회 OR 대통령 OR 여야 OR 정당",
        "en": "politics OR election OR government",
        "n_ko": 10, "n_en": 2,
    },
    "사회": {
        "ko": "사회 OR 노동 OR 교육 OR 복지 OR 사건사고",
        "en": "society OR social issues OR labor OR education",
        "n_ko": 10, "n_en": 2,
    },
    "외교안보": {
        "ko": "외교 OR 안보 OR 국방 OR 북한 OR 한미 OR 한중 OR 한일",
        "en": "Korea diplomacy OR North Korea OR Korea security OR geopolitics OR alliance",
        "n_ko": 12, "n_en": 10,
    },
    "해외 사건사고": {
        "ko": "해외 사건 OR 해외 사고 OR 국제 속보 OR 외신 사건",
        "en": "breaking news OR disaster OR accident OR crime OR attack",
        "n_ko": 5, "n_en": 5,
    },
}

LOCALES = {
    "ko": "hl=ko&gl=KR&ceid=KR:ko",
    "en": "hl=en-US&gl=US&ceid=US:en",
}

# 키워드 추출용 불용어
STOPWORDS = {
    "속보", "단독", "종합", "오늘", "올해", "지난", "대한", "위해", "관련", "대해",
    "이번", "최근", "경우", "우리", "그리고", "하지만", "이날", "기자", "뉴스",
    "사진", "영상", "보도", "외신", "오전", "오후", "지난해", "내년", "당시",
    "the", "and", "for", "with", "that", "this", "from", "says", "after",
    "over", "new", "are", "will", "amid", "한국", "korea", "korean",
}

st.set_page_config(page_title="뉴스 현황판", page_icon="📰", layout="wide")


# ─────────────────────────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────────────────────────
def strip_html(raw: str) -> str:
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_source_and_title(entry):
    title = entry.get("title", "").strip()
    source = ""
    src = entry.get("source")
    if src:
        source = (src.get("title") if hasattr(src, "get") else getattr(src, "title", "")) or ""
    if " - " in title:
        head, tail = title.rsplit(" - ", 1)
        if not source:
            source = tail.strip()
        title = head.strip()
    return title, (source or "기타")


def to_kst(entry):
    tm = entry.get("published_parsed") or entry.get("updated_parsed")
    if not tm:
        return None
    return datetime(*tm[:6], tzinfo=timezone.utc).astimezone(KST)


@st.cache_data(ttl=600, show_spinner=False)
def fetch_raw(query: str, locale: str, limit: int) -> pd.DataFrame:
    q = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={q}&{LOCALES[locale]}"
    feed = feedparser.parse(url)
    rows = []
    for e in feed.entries[:limit]:
        title, source = parse_source_and_title(e)
        summary = strip_html(e.get("summary", ""))
        if summary[:30] == title[:30]:
            summary = ""
        rows.append({
            "헤드라인": title,
            "구분": "국내" if locale == "ko" else "외신",
            "언론사": source,
            "주요내용": summary,
            "기사시간": to_kst(e),
            "링크": e.get("link", ""),
        })
    return pd.DataFrame(rows)


# ── 중복 기사 묶기 ─────────────────────────────────────────────
def norm_title(t: str) -> str:
    t = re.sub(r"\[[^\]]*\]", " ", t)               # [속보] 류 제거
    t = re.sub(r"[^가-힣A-Za-z0-9 ]", " ", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def token_set(t: str):
    return set(w for w in norm_title(t).split() if len(w) >= 2)


def is_similar(a: str, b: str) -> bool:
    ta, tb = token_set(a), token_set(b)
    if ta and tb:
        jac = len(ta & tb) / len(ta | tb)
        if jac >= 0.5:
            return True
    return SequenceMatcher(None, norm_title(a), norm_title(b)).ratio() >= 0.7


def dedupe(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.sort_values("기사시간", ascending=False, na_position="last").reset_index(drop=True)
    titles = df["헤드라인"].tolist()
    used = [False] * len(df)
    reps = []
    for i in range(len(df)):
        if used[i]:
            continue
        used[i] = True
        dup = 0
        for j in range(i + 1, len(df)):
            if not used[j] and is_similar(titles[i], titles[j]):
                used[j] = True
                dup += 1
        row = df.iloc[i].copy()
        row["_dupes"] = dup
        reps.append(row)
    out = pd.DataFrame(reps)
    out["보도현황"] = out["_dupes"].apply(lambda n: f"외 {n}건 보도 중" if n > 0 else "")
    return out


def build_category(cat: str, cutoff: datetime) -> pd.DataFrame:
    cfg = CATEGORY_CONFIG[cat]
    dom = fetch_raw(cfg["ko"], "ko", cfg["n_ko"] + 8)
    forg = fetch_raw(cfg["en"], "en", cfg["n_en"] + 8)

    def trim(df, n):
        if df.empty:
            return df
        df = df[df["기사시간"].notna() & (df["기사시간"] >= cutoff)]
        return df.sort_values("기사시간", ascending=False).head(n)

    dom, forg = trim(dom, cfg["n_ko"]), trim(forg, cfg["n_en"])
    combined = pd.concat([dom, forg], ignore_index=True)
    return dedupe(combined)


# ── 키워드 추출 ────────────────────────────────────────────────
JOSA = ("으로", "에서", "에게", "까지", "부터", "이라", "라며", "라고")

def clean_token(tok: str) -> str:
    if re.fullmatch(r"[가-힣]+", tok):
        for p in JOSA:
            if tok.endswith(p) and len(tok) > len(p) + 1:
                return tok[:-len(p)]
        if len(tok) >= 3 and tok[-1] in "은는이가을를에의로와과도만":
            return tok[:-1]
    return tok.lower()


def keyword_counts(cat_dfs: dict, top_n: int = 10) -> Counter:
    counter = Counter()
    for df in cat_dfs.values():
        if df.empty:
            continue
        for _, row in df.iterrows():
            weight = 1 + int(row.get("_dupes", 0))   # 많이 보도된 기사일수록 가중
            for tok in re.findall(r"[가-힣A-Za-z0-9]+", str(row["헤드라인"])):
                tok = clean_token(tok)
                if len(tok) < 2 or tok in STOPWORDS:
                    continue
                counter[tok] += weight
    return Counter(dict(counter.most_common(top_n)))


# ─────────────────────────────────────────────────────────────
# 사이드바
# ─────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ 설정")
    selected = st.multiselect(
        "카테고리 선택",
        options=list(CATEGORY_CONFIG.keys()),
        default=list(CATEGORY_CONFIG.keys()),
    )
    hours = st.slider("최근 N시간 이내", 1, 72, 24, step=1)
    keyword = st.text_input("키워드 필터 (제목/내용)", "")
    if st.button("🔄 새로고침 (캐시 비우기)"):
        st.cache_data.clear()
        st.rerun()
    st.caption("데이터: 구글 뉴스 RSS · 10분 캐시")

cutoff = datetime.now(KST) - timedelta(hours=hours)

# ── 데이터 수집 (전체 카테고리) ────────────────────────────────
cat_dfs = {}
if selected:
    with st.spinner("뉴스 불러오는 중..."):
        for cat in selected:
            cat_dfs[cat] = build_category(cat, cutoff)

# ─────────────────────────────────────────────────────────────
# 상단: 제목 + 키워드 도넛
# ─────────────────────────────────────────────────────────────
col_title, col_chart = st.columns([2, 1])
with col_title:
    st.title("📰 국내·국제 주요 뉴스 현황판")
    st.caption(f"마지막 갱신: {datetime.now(KST):%Y-%m-%d %H:%M:%S} KST")
with col_chart:
    kw = keyword_counts(cat_dfs, top_n=10) if cat_dfs else Counter()
    if kw:
        fig = go.Figure(data=[go.Pie(
            labels=list(kw.keys()),
            values=list(kw.values()),
            hole=0.45,
            textinfo="label",
            textposition="inside",
        )])
        fig.update_layout(
            title=dict(text="🔑 최다 언급 키워드", x=0.5, font=dict(size=14)),
            margin=dict(t=40, b=10, l=10, r=10),
            height=280, showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.caption("키워드 데이터 없음")

st.divider()

# ─────────────────────────────────────────────────────────────
# 카테고리 탭
# ─────────────────────────────────────────────────────────────
COLUMN_CONFIG = {
    "헤드라인": st.column_config.TextColumn("헤드라인", width="large"),
    "보도현황": st.column_config.TextColumn("보도현황", width="small"),
    "구분":     st.column_config.TextColumn("구분", width="small"),
    "언론사":   st.column_config.TextColumn("언론사", width="small"),
    "주요내용": st.column_config.TextColumn("주요내용", width="medium"),
    "기사시간": st.column_config.DatetimeColumn("기사시간", format="MM-DD HH:mm", width="small"),
    "링크":     st.column_config.LinkColumn("링크", display_text="원문 →", width="small"),
}
SHOW_COLS = ["헤드라인", "보도현황", "구분", "언론사", "주요내용", "기사시간", "링크"]


def render_table(df: pd.DataFrame):
    if df is None or df.empty:
        st.info("조건에 맞는 기사가 없어.")
        return
    view = df.copy()
    if keyword.strip():
        kw = keyword.strip()
        mask = view["헤드라인"].str.contains(kw, case=False, na=False) | \
               view["주요내용"].str.contains(kw, case=False, na=False)
        view = view[mask]
    if view.empty:
        st.info("조건에 맞는 기사가 없어.")
        return
    st.dataframe(
        view[SHOW_COLS], column_config=COLUMN_CONFIG,
        hide_index=True, use_container_width=True,
    )
    st.caption(f"표시 {len(view)}건")


if not selected:
    st.warning("왼쪽에서 카테고리를 하나 이상 선택해줘.")
else:
    tabs = st.tabs(selected)
    for tab, cat in zip(tabs, selected):
        with tab:
            render_table(cat_dfs.get(cat))
