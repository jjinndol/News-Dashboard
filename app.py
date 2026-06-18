# -*- coding: utf-8 -*-
"""
국내/국제 주요 뉴스 현황판 (Streamlit) v3
- 6개 대분류, 국내/외신 혼합, 중복기사 묶기
- 화제성(보도 건수) 순 정렬
- 키워드 도넛차트 + 뉴스 핫스팟 지구본
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
# 카테고리 설정 (숫자만 바꾸면 개수 조정됨)
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

# 합성어 → 단일 키워드 치환 (파편화 방지)
COMPOUND_MAP = {
    "middle east": "중동", "north korea": "북한", "south korea": "한국",
    "united states": "미국", "white house": "백악관", "wall street": "월가",
    "european union": "유럽연합", "federal reserve": "연준", "supreme court": "대법원",
    "hong kong": "홍콩", "new york": "뉴욕", "saudi arabia": "사우디",
    "united nations": "유엔", "donald trump": "트럼프", "xi jinping": "시진핑",
    "vladimir putin": "푸틴", "kim jong": "김정은",
}

# 불용어 (전치사/관사/대명사/조동사/뉴스용어/지명파편 등)
STOPWORDS = {
    # 영어 기능어
    "the", "a", "an", "and", "or", "but", "nor", "so", "yet", "as", "if", "than",
    "then", "that", "this", "these", "those", "such", "of", "in", "on", "at", "to",
    "by", "for", "with", "from", "into", "onto", "over", "under", "about", "after",
    "before", "between", "against", "amid", "during", "through", "across", "toward",
    "towards", "upon", "within", "without", "per", "via", "off", "out", "up", "down",
    "it", "its", "he", "she", "they", "them", "his", "her", "their", "we", "us",
    "our", "you", "your", "i", "my", "me", "is", "are", "was", "were", "be", "been",
    "being", "has", "have", "had", "do", "does", "did", "will", "would", "can",
    "could", "should", "may", "might", "must", "says", "said", "say", "get", "gets",
    "got", "make", "made", "set", "see", "seen", "take", "takes", "new", "more",
    "most", "all", "no", "not", "now", "just", "also", "here", "there", "what",
    "when", "where", "who", "why", "how", "which", "amid", "amid",
    # 뉴스 용어
    "news", "report", "reports", "update", "live", "breaking", "watch", "video",
    "photo", "latest", "exclusive", "vs", "ago",
    # 지명 파편 (지구본이 따로 처리)
    "middle", "east", "west", "north", "south", "united", "states", "house",
    "street", "korea", "korean", "china", "chinese", "japan", "japanese",
    # 한국어
    "속보", "단독", "종합", "오늘", "올해", "지난", "대한", "위해", "관련", "대해",
    "이번", "최근", "경우", "우리", "그리고", "하지만", "이날", "기자", "뉴스",
    "사진", "영상", "보도", "외신", "오전", "오후", "지난해", "내년", "당시", "한국",
}

# 핫스팟 지명 사전: 이름 -> (위도, 경도, [별칭])
GAZETTEER = {
    "한국": (37.57, 126.98, ["서울", "seoul", "south korea"]),
    "북한": (39.02, 125.75, ["북한", "평양", "north korea", "pyongyang"]),
    "미국": (38.90, -77.04, ["미국", "워싱턴", "백악관", "united states", "washington", "white house"]),
    "중국": (39.90, 116.40, ["중국", "베이징", "china", "beijing"]),
    "일본": (35.68, 139.69, ["일본", "도쿄", "japan", "tokyo"]),
    "대만": (25.03, 121.56, ["대만", "타이완", "taiwan", "taipei"]),
    "홍콩": (22.32, 114.17, ["홍콩", "hong kong"]),
    "러시아": (55.75, 37.62, ["러시아", "모스크바", "russia", "moscow", "putin", "푸틴"]),
    "우크라이나": (50.45, 30.52, ["우크라", "키이우", "ukraine", "kyiv", "kiev"]),
    "이란": (35.69, 51.39, ["이란", "테헤란", "iran", "tehran"]),
    "이스라엘": (31.78, 35.22, ["이스라엘", "israel", "jerusalem"]),
    "가자": (31.50, 34.47, ["가자", "gaza", "팔레스타인", "palestin"]),
    "레바논": (33.89, 35.50, ["레바논", "베이루트", "lebanon", "beirut", "헤즈볼라", "hezbollah"]),
    "시리아": (33.51, 36.29, ["시리아", "syria", "damascus"]),
    "사우디": (24.71, 46.68, ["사우디", "리야드", "saudi", "riyadh"]),
    "예멘": (15.35, 44.21, ["예멘", "yemen", "후티", "houthi"]),
    "이라크": (33.31, 44.36, ["이라크", "iraq", "baghdad"]),
    "튀르키예": (39.93, 32.86, ["튀르키예", "터키", "turkey", "ankara"]),
    "인도": (28.61, 77.21, ["인도", "뉴델리", "india", "delhi"]),
    "파키스탄": (33.69, 73.06, ["파키스탄", "pakistan", "islamabad"]),
    "영국": (51.51, -0.13, ["영국", "런던", "britain", "london", "uk"]),
    "프랑스": (48.86, 2.35, ["프랑스", "파리", "france", "paris"]),
    "독일": (52.52, 13.40, ["독일", "베를린", "germany", "berlin"]),
    "이탈리아": (41.90, 12.50, ["이탈리아", "로마", "italy", "rome"]),
    "스페인": (40.42, -3.70, ["스페인", "마드리드", "spain", "madrid"]),
    "EU": (50.85, 4.35, ["유럽연합", "브뤼셀", "european union", "brussels"]),
    "폴란드": (52.23, 21.01, ["폴란드", "poland", "warsaw"]),
    "캐나다": (45.42, -75.70, ["캐나다", "canada", "ottawa"]),
    "멕시코": (19.43, -99.13, ["멕시코", "mexico"]),
    "브라질": (-15.79, -47.88, ["브라질", "brazil", "brasilia"]),
    "아르헨티나": (-34.60, -58.38, ["아르헨티나", "argentina"]),
    "호주": (-33.87, 151.21, ["호주", "시드니", "australia", "sydney"]),
    "베트남": (21.03, 105.85, ["베트남", "vietnam", "hanoi"]),
    "인도네시아": (-6.20, 106.85, ["인도네시아", "indonesia", "jakarta"]),
    "필리핀": (14.60, 120.98, ["필리핀", "philippines", "manila"]),
    "태국": (13.75, 100.50, ["태국", "thailand", "bangkok"]),
    "싱가포르": (1.35, 103.82, ["싱가포르", "singapore"]),
    "이집트": (30.04, 31.24, ["이집트", "egypt", "cairo"]),
    "남아공": (-25.75, 28.19, ["남아공", "south africa"]),
    "나이지리아": (9.08, 7.40, ["나이지리아", "nigeria"]),
    "뉴욕": (40.71, -74.01, ["뉴욕", "맨해튼", "new york", "월가", "wall street"]),
    "호르무즈": (26.57, 56.25, ["호르무즈", "hormuz"]),
}

st.set_page_config(page_title="뉴스 현황판", page_icon="📰", layout="wide")


# ─────────────────────────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────────────────────────
def strip_html(raw):
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


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
def fetch_raw(query, locale, limit):
    q = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={q}&{LOCALES[locale]}"
    feed = feedparser.parse(url)
    rows = []
    for rank, e in enumerate(feed.entries[:limit]):
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
            "_rank": rank,            # 구글 뉴스 원래 순서(화제성)
        })
    return pd.DataFrame(rows)


# ── 중복 기사 묶기 ─────────────────────────────────────────────
def norm_title(t):
    t = re.sub(r"\[[^\]]*\]", " ", t)
    t = re.sub(r"[^가-힣A-Za-z0-9 ]", " ", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def token_set(t):
    return set(w for w in norm_title(t).split() if len(w) >= 2)


def is_similar(a, b):
    ta, tb = token_set(a), token_set(b)
    if ta and tb and len(ta & tb) / len(ta | tb) >= 0.5:
        return True
    return SequenceMatcher(None, norm_title(a), norm_title(b)).ratio() >= 0.7


def dedupe(df):
    if df.empty:
        return df
    df = df.reset_index(drop=True)
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


def build_category(cat, cutoff):
    cfg = CATEGORY_CONFIG[cat]
    dom = fetch_raw(cfg["ko"], "ko", cfg["n_ko"] + 8)
    forg = fetch_raw(cfg["en"], "en", cfg["n_en"] + 8)

    def trim(df, n):
        if df.empty:
            return df
        df = df[df["기사시간"].notna() & (df["기사시간"] >= cutoff)]
        return df.sort_values("_rank").head(n)

    combined = pd.concat([trim(dom, cfg["n_ko"]), trim(forg, cfg["n_en"])], ignore_index=True)
    out = dedupe(combined)
    if out.empty:
        return out
    # 화제성 순: 보도 건수(_dupes) 많은 순 → 구글 원래 순서(_rank) → 최신순
    out["_score"] = out["_dupes"]
    out = out.sort_values(
        ["_score", "_rank", "기사시간"],
        ascending=[False, True, False],
    ).reset_index(drop=True)
    return out


# ── 키워드 추출 ────────────────────────────────────────────────
JOSA = ("으로", "에서", "에게", "까지", "부터", "이라", "라며", "라고")

def clean_token(tok):
    if re.fullmatch(r"[가-힣]+", tok):
        for p in JOSA:
            if tok.endswith(p) and len(tok) > len(p) + 1:
                return tok[:-len(p)]
        if len(tok) >= 3 and tok[-1] in "은는이가을를에의로와과도만":
            return tok[:-1]
    return tok.lower()


def extract_tokens(title):
    t = title.lower()
    for k, v in COMPOUND_MAP.items():
        t = t.replace(k, " " + v + " ")
    out = []
    for tok in re.findall(r"[가-힣A-Za-z0-9]+", t):
        tok = clean_token(tok)
        if len(tok) < 2 or tok in STOPWORDS or tok.isdigit():
            continue
        out.append(tok)
    return out


def keyword_counts(cat_dfs, top_n=10):
    counter = Counter()
    for df in cat_dfs.values():
        if df.empty:
            continue
        for _, row in df.iterrows():
            weight = 1 + int(row.get("_dupes", 0))
            for tok in extract_tokens(str(row["헤드라인"])):
                counter[tok] += weight
    return Counter(dict(counter.most_common(top_n)))


def hotspot_counts(cat_dfs):
    acc = {}
    for df in cat_dfs.values():
        if df.empty:
            continue
        for _, row in df.iterrows():
            weight = 1 + int(row.get("_dupes", 0))
            text = (str(row["헤드라인"]) + " " + str(row.get("주요내용", ""))).lower()
            for place, (lat, lon, aliases) in GAZETTEER.items():
                hit = False
                for a in [place] + aliases:
                    al = a.lower()
                    if re.search(r"[가-힣]", al):
                        if al in text:
                            hit = True
                            break
                    elif re.search(r"\b" + re.escape(al) + r"\b", text):
                        hit = True
                        break
                if hit:
                    lat0, lon0, c = acc.get(place, (lat, lon, 0))
                    acc[place] = (lat0, lon0, c + weight)
    return acc


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
    st.caption(f"마지막 갱신: {datetime.now(KST):%Y-%m-%d %H:%M:%S} KST · 화제성(보도건수) 순")
with col_chart:
    kw = keyword_counts(cat_dfs, top_n=10) if cat_dfs else Counter()
    if kw:
        fig = go.Figure(data=[go.Pie(
            labels=list(kw.keys()), values=list(kw.values()),
            hole=0.45, textinfo="label", textposition="inside",
        )])
        fig.update_layout(
            title=dict(text="🔑 최다 언급 키워드", x=0.5, font=dict(size=14)),
            margin=dict(t=40, b=10, l=10, r=10), height=280, showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.caption("키워드 데이터 없음")

# ── 뉴스 핫스팟 지구본 ─────────────────────────────────────────
spots = hotspot_counts(cat_dfs) if cat_dfs else {}
if spots:
    items = sorted(spots.items(), key=lambda kv: kv[1][2], reverse=True)[:15]
    names = [k for k, _ in items]
    lats = [v[0] for _, v in items]
    lons = [v[1] for _, v in items]
    cnts = [v[2] for _, v in items]
    mx = max(cnts) or 1
    sizes = [12 + 32 * (c / mx) for c in cnts]
    globe = go.Figure(go.Scattergeo(
        lon=lons, lat=lats,
        text=[f"{n} ({c})" for n, c in zip(names, cnts)],
        mode="markers+text", textposition="top center",
        textfont=dict(size=10, color="white"),
        marker=dict(size=sizes, color=cnts, colorscale="YlOrRd",
                    line=dict(width=0.5, color="white"), opacity=0.85,
                    showscale=False),
        hovertemplate="%{text}<extra></extra>",
    ))
    globe.update_geos(
        projection_type="orthographic",
        projection_rotation=dict(lon=110, lat=25),
        showland=True, landcolor="rgb(45,45,48)",
        showocean=True, oceancolor="rgb(12,20,38)",
        showcountries=True, countrycolor="rgb(80,80,85)",
        showcoastlines=False, bgcolor="rgba(0,0,0,0)",
    )
    globe.update_layout(
        height=470, margin=dict(t=36, b=0, l=0, r=0),
        title=dict(text="🌍 주요 뉴스 핫스팟 (언급량 기준)", x=0.5, font=dict(size=15)),
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(globe, use_container_width=True)

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


def render_table(df):
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
    st.dataframe(view[SHOW_COLS], column_config=COLUMN_CONFIG,
                 hide_index=True, use_container_width=True)
    st.caption(f"표시 {len(view)}건")


if not selected:
    st.warning("왼쪽에서 카테고리를 하나 이상 선택해줘.")
else:
    tabs = st.tabs(selected)
    for tab, cat in zip(tabs, selected):
        with tab:
            render_table(cat_dfs.get(cat))
