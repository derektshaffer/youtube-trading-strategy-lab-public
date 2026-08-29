import streamlit as st


def inject_research_glass_theme() -> None:
    """Scanner-inspired glass theme for the Trading Intelligence Lab.

    Presentation only: no research, strategy, backtest, validation, or
    execution logic is changed here.
    """
    st.markdown(
        """
        <style>
        :root {
            --til-bg-0: #050c15;
            --til-bg-1: #07111d;
            --til-bg-2: #0a1726;
            --til-panel: rgba(10, 23, 38, .74);
            --til-panel-strong: rgba(11, 27, 45, .92);
            --til-inner: rgba(18, 38, 60, .42);
            --til-line: rgba(112, 151, 190, .14);
            --til-line-soft: rgba(112, 151, 190, .09);
            --til-line-strong: rgba(112, 165, 211, .27);
            --til-text: #f2f6fb;
            --til-text-soft: #d7e1eb;
            --til-muted: #8ea2b9;
            --til-muted-2: #6f839b;
            --til-green: #43d17d;
            --til-green-soft: rgba(67, 209, 125, .09);
            --til-green-line: rgba(67, 209, 125, .32);
            --til-blue: #6ebbe6;
            --til-purple: #a887d8;
            --til-red: #e96775;
            --til-amber: #dcb667;
            --til-radius: 13px;
            --til-shadow: 0 16px 42px rgba(0, 0, 0, .18);
        }

        html,
        body,
        [data-testid="stAppViewContainer"],
        .stApp {
            background:
                radial-gradient(circle at 88% -12%, rgba(68, 116, 157, .08), transparent 34%),
                linear-gradient(180deg, var(--til-bg-0) 0%, var(--til-bg-1) 52%, #06101a 100%) !important;
            color: var(--til-text) !important;
        }

        [data-testid="stHeader"] {
            background: rgba(5, 12, 21, .64) !important;
            backdrop-filter: blur(18px) !important;
            -webkit-backdrop-filter: blur(18px) !important;
            border-bottom: 1px solid rgba(112,151,190,.055) !important;
        }

        .block-container {
            max-width: 1580px !important;
            padding-top: 1.15rem !important;
            padding-left: 1.45rem !important;
            padding-right: 1.45rem !important;
            padding-bottom: 2.8rem !important;
        }

        /* ---------- Compact product bar ---------- */
        .til-hero {
            position: relative;
            overflow: hidden;
            display: flex;
            align-items: center;
            gap: 13px;
            padding: 10px 14px !important;
            margin: 2px 0 16px !important;
            min-height: 54px !important;
            border: 1px solid rgba(112,151,190,.10) !important;
            border-radius: 12px !important;
            background:
                linear-gradient(135deg, rgba(10,25,42,.72), rgba(7,18,31,.52)) !important;
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,.018),
                0 7px 22px rgba(0,0,0,.075) !important;
            backdrop-filter: blur(16px) !important;
            -webkit-backdrop-filter: blur(16px) !important;
        }
        .til-hero::after {
            content: "";
            position: absolute;
            left: 0;
            top: 12px;
            bottom: 12px;
            width: 2px;
            border-radius: 999px;
            background: rgba(67,209,125,.66);
        }
        .til-kicker {
            display: inline-flex;
            align-items: center;
            gap: 7px;
            margin: 0 10px 0 0;
            color: #7489a1;
            font-size: .63rem;
            line-height: 1;
            font-weight: 850;
            letter-spacing: .12em;
            text-transform: uppercase;
            white-space: nowrap;
        }
        .til-brand-mark {
            width: 19px;
            height: 19px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border-radius: 6px;
            color: #70d99a;
            border: 1px solid rgba(67,209,125,.17);
            background: rgba(67,209,125,.045);
            font-size: 10px;
            letter-spacing: 0;
        }
        .til-title {
            display: inline-block;
            color: #eaf1f8 !important;
            font-size: 1.04rem !important;
            line-height: 1 !important;
            font-weight: 760 !important;
            letter-spacing: -.022em !important;
            text-shadow: none !important;
        }
        .til-sub {
            display: none !important;
        }

        /* ---------- Per-page workspace header ---------- */
        .til-pagehead {
            position: relative;
            display: flex;
            align-items: stretch;
            justify-content: space-between;
            gap: 24px;
            overflow: hidden;
            padding: 20px 22px 19px;
            margin: 0 0 22px;
            border: 1px solid rgba(112,151,190,.13);
            border-radius: 14px;
            background:
                radial-gradient(circle at 98% 0%, rgba(67,209,125,.055), transparent 28%),
                linear-gradient(145deg, rgba(11,27,45,.78), rgba(7,18,31,.60));
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,.022),
                0 10px 30px rgba(0,0,0,.09);
        }
        .til-pagehead::before {
            content: "";
            position: absolute;
            left: 0;
            top: 17px;
            bottom: 17px;
            width: 2px;
            border-radius: 999px;
            background: rgba(67,209,125,.72);
        }
        .til-pagehead-main {
            min-width: 0;
            max-width: 1000px;
        }
        .til-page-eyebrow {
            color: #71879f;
            font-size: .66rem;
            line-height: 1;
            font-weight: 850;
            letter-spacing: .13em;
            text-transform: uppercase;
            margin-bottom: 8px;
        }
        .til-page-eyebrow span {
            color: rgba(67,209,125,.68);
            padding: 0 4px;
        }
        .til-page-title {
            color: #f1f5f9;
            font-size: 2rem;
            line-height: 1.06;
            font-weight: 805;
            letter-spacing: -.042em;
        }
        .til-page-sub {
            margin-top: 8px;
            max-width: 900px;
            color: #91a4b9;
            font-size: .94rem;
            line-height: 1.54;
        }
        .til-page-step {
            align-self: center;
            flex: 0 0 auto;
            padding-right: 4px;
            color: rgba(132,153,176,.14);
            font-size: 4.35rem;
            line-height: .9;
            font-weight: 900;
            letter-spacing: -.08em;
            user-select: none;
        }

        /* ---------- Sidebar product identity ---------- */
        .til-sidebrand {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 8px 7px 10px;
            margin: 0 0 3px;
        }
        .til-sidebrand-mark {
            width: 31px;
            height: 31px;
            display: flex;
            align-items: center;
            justify-content: center;
            flex: 0 0 31px;
            border-radius: 9px;
            color: #75dda0;
            border: 1px solid rgba(67,209,125,.18);
            background: rgba(67,209,125,.05);
            font-size: 14px;
        }
        .til-sidebrand-name {
            color: #edf3f8;
            font-size: .92rem;
            line-height: 1.1;
            font-weight: 760;
            letter-spacing: -.018em;
        }
        .til-sidebrand-sub {
            margin-top: 3px;
            color: #71869e;
            font-size: .68rem;
            line-height: 1;
            font-weight: 680;
        }
        .til-sideflow {
            margin: 0 7px 20px;
            padding: 7px 9px;
            border: 1px solid rgba(112,151,190,.08);
            border-radius: 8px;
            color: #657a92;
            background: rgba(12,28,46,.22);
            font-size: .67rem;
            font-weight: 650;
            letter-spacing: .025em;
        }

        /* ---------- Page hierarchy ---------- */
        h1, h2, h3, h4, h5, h6 {
            color: var(--til-text) !important;
            letter-spacing: -.025em !important;
        }
        h1 {
            font-weight: 820 !important;
        }
        h2 {
            margin-top: 1.9rem !important;
            margin-bottom: .85rem !important;
            padding-bottom: .52rem !important;
            border-bottom: 1px solid rgba(112,151,190,.095) !important;
            font-size: 1.68rem !important;
            font-weight: 790 !important;
        }
        h3 {
            margin-top: 1.35rem !important;
            font-size: 1.18rem !important;
            font-weight: 760 !important;
        }
        p, li {
            color: var(--til-text-soft);
            line-height: 1.58;
        }
        [data-testid="stCaptionContainer"],
        small {
            color: var(--til-muted) !important;
            line-height: 1.48 !important;
        }
        a {
            color: var(--til-blue) !important;
        }
        hr {
            border-color: rgba(112,151,190,.095) !important;
            margin-top: 1.25rem !important;
            margin-bottom: 1.25rem !important;
        }

        /* ---------- Sidebar ---------- */
        [data-testid="stSidebar"] {
            background:
                linear-gradient(180deg, rgba(6,16,28,.99), rgba(5,13,23,.99)) !important;
            border-right: 1px solid rgba(112,151,190,.11) !important;
        }
        [data-testid="stSidebar"] > div:first-child {
            background: transparent !important;
        }
        [data-testid="stSidebar"] .block-container {
            padding-top: 1.10rem !important;
            padding-left: .90rem !important;
            padding-right: .78rem !important;
        }
        [data-testid="stSidebar"] h3 {
            color: #eff4fa !important;
            font-size: 1.20rem !important;
            font-weight: 790 !important;
            letter-spacing: -.027em !important;
            margin: 0 0 .62rem !important;
        }

        .st-key-til_workspace_section div[role="radiogroup"] {
            gap: .08rem !important;
        }
        .st-key-til_workspace_section div[role="radiogroup"] > label {
            position: relative !important;
            display: flex !important;
            align-items: center !important;
            min-height: 2.34rem !important;
            padding: .36rem .55rem .36rem .76rem !important;
            margin: 0 !important;
            border: 1px solid transparent !important;
            border-radius: 8px !important;
            background: transparent !important;
            box-shadow: none !important;
            transition:
                background .14s ease,
                border-color .14s ease,
                color .14s ease !important;
        }
        .st-key-til_workspace_section div[role="radiogroup"] > label::after {
            content: "";
            position: absolute;
            left: -1px;
            top: 8px;
            bottom: 8px;
            width: 2px;
            border-radius: 999px;
            background: transparent;
            transition: background .14s ease;
        }
        .st-key-til_workspace_section div[role="radiogroup"] > label:hover {
            background: rgba(18,38,60,.30) !important;
            border-color: rgba(112,151,190,.075) !important;
        }
        .st-key-til_workspace_section div[role="radiogroup"] > label:has(input:checked) {
            background:
                linear-gradient(90deg, rgba(67,209,125,.085), rgba(18,38,60,.18)) !important;
            border-color: rgba(67,209,125,.12) !important;
            box-shadow: none !important;
        }
        .st-key-til_workspace_section div[role="radiogroup"] > label:has(input:checked)::after {
            background: var(--til-green);
        }
        .st-key-til_workspace_section div[role="radiogroup"] > label p {
            color: #aab9ca !important;
            font-size: .91rem !important;
            font-weight: 620 !important;
            letter-spacing: -.012em !important;
            line-height: 1.12 !important;
        }
        .st-key-til_workspace_section div[role="radiogroup"] > label:has(input:checked) p {
            color: #f0f5fa !important;
            font-weight: 710 !important;
        }

        .st-key-til_workspace_section input[type="radio"],
        .st-key-til_workspace_section div[role="radiogroup"] > label > div:first-child {
            position: absolute !important;
            opacity: 0 !important;
            width: 1px !important;
            height: 1px !important;
            overflow: hidden !important;
            pointer-events: none !important;
        }

        .st-key-til_workspace_section div[role="radiogroup"] > label:nth-child(1)::before,
        .st-key-til_workspace_section div[role="radiogroup"] > label:nth-child(4)::before,
        .st-key-til_workspace_section div[role="radiogroup"] > label:nth-child(9)::before,
        .st-key-til_workspace_section div[role="radiogroup"] > label:nth-child(13)::before {
            color: #637991 !important;
            font-size: .61rem !important;
            font-weight: 850 !important;
            letter-spacing: .14em !important;
        }

        [data-testid="stSidebar"] hr {
            border-color: rgba(112,151,190,.075) !important;
        }
        [data-testid="stSidebar"] [data-testid="stCaptionContainer"] {
            color: #657a91 !important;
        }

        /* ---------- Surfaces ---------- */
        .til-card,
        [data-testid="stExpander"] details,
        div[data-testid="stDataFrame"],
        div[data-testid="stTable"],
        [data-testid="stFileUploader"],
        [data-testid="stStatusWidget"] {
            border-color: var(--til-line) !important;
            background:
                linear-gradient(145deg, rgba(11,27,45,.70), rgba(8,20,34,.56)) !important;
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,.018),
                0 8px 26px rgba(0,0,0,.085) !important;
            backdrop-filter: blur(13px) !important;
            -webkit-backdrop-filter: blur(13px) !important;
        }

        .til-card {
            min-height: 124px !important;
            padding: 17px 18px !important;
            margin-bottom: 12px !important;
            border: 1px solid var(--til-line) !important;
            border-radius: 12px !important;
            transition:
                border-color .14s ease,
                background .14s ease !important;
        }
        .til-card:hover {
            border-color: rgba(112,165,211,.20) !important;
            background:
                linear-gradient(145deg, rgba(12,30,49,.76), rgba(8,20,34,.58)) !important;
            transform: none !important;
        }
        .til-card strong {
            color: #edf3f9 !important;
            font-weight: 740 !important;
        }
        .muted {
            color: var(--til-muted) !important;
        }

        [data-testid="stExpander"] details {
            border: 1px solid rgba(112,151,190,.13) !important;
            border-radius: 10px !important;
            overflow: hidden !important;
            box-shadow: none !important;
        }
        [data-testid="stExpander"] summary {
            min-height: 44px !important;
            color: #dce6f0 !important;
            font-weight: 650 !important;
        }
        [data-testid="stExpander"] details[open] {
            border-color: rgba(112,165,211,.20) !important;
        }

        div[data-testid="stDataFrame"],
        div[data-testid="stTable"] {
            border: 1px solid rgba(112,151,190,.12) !important;
            border-radius: 10px !important;
            overflow: hidden !important;
            box-shadow: none !important;
        }

        /* ---------- Metrics ---------- */
        [data-testid="stMetric"] {
            min-height: 106px !important;
            padding: .88rem .95rem !important;
            border: 1px solid rgba(112,151,190,.12) !important;
            border-radius: 11px !important;
            background:
                linear-gradient(145deg, rgba(15,33,52,.46), rgba(9,23,38,.40)) !important;
            box-shadow: inset 0 1px 0 rgba(255,255,255,.014) !important;
        }
        [data-testid="stMetricLabel"] {
            color: #8499b0 !important;
            font-size: .83rem !important;
            font-weight: 620 !important;
        }
        [data-testid="stMetricValue"] {
            color: #eef4fa !important;
            font-weight: 670 !important;
            letter-spacing: -.03em !important;
        }
        [data-testid="stMetricDelta"] {
            font-size: .78rem !important;
        }

        /* ---------- Tabs ---------- */
        [data-baseweb="tab-list"] {
            gap: 1px !important;
            padding: 0 0 4px !important;
            border: 0 !important;
            border-bottom: 1px solid rgba(112,151,190,.095) !important;
            border-radius: 0 !important;
            background: transparent !important;
        }
        [data-baseweb="tab"] {
            min-height: 38px !important;
            padding-left: 12px !important;
            padding-right: 12px !important;
            border-radius: 7px 7px 0 0 !important;
            color: #8fa3b8 !important;
            font-size: .90rem !important;
            font-weight: 650 !important;
            background: transparent !important;
        }
        [aria-selected="true"][data-baseweb="tab"] {
            color: #eef5fa !important;
            background: rgba(18,38,60,.28) !important;
            box-shadow: none !important;
        }
        [data-baseweb="tab-highlight"] {
            height: 2px !important;
            background-color: rgba(67,209,125,.82) !important;
        }

        /* ---------- Inputs ---------- */
        div[data-baseweb="select"] > div,
        [data-testid="stTextInput"] input,
        [data-testid="stNumberInput"] input,
        [data-testid="stTextArea"] textarea {
            color: #eaf1f8 !important;
            min-height: 40px !important;
            border-color: rgba(112,151,190,.16) !important;
            border-radius: 9px !important;
            background: rgba(9,23,38,.68) !important;
            box-shadow: none !important;
        }
        div[data-baseweb="select"] > div:hover,
        [data-testid="stTextInput"] input:hover,
        [data-testid="stTextArea"] textarea:hover {
            border-color: rgba(112,165,211,.27) !important;
        }
        div[data-baseweb="select"] > div:focus-within,
        [data-testid="stTextInput"] input:focus,
        [data-testid="stNumberInput"] input:focus,
        [data-testid="stTextArea"] textarea:focus {
            border-color: rgba(67,209,125,.34) !important;
            box-shadow: 0 0 0 2px rgba(67,209,125,.055) !important;
        }

        [data-testid="stFileUploader"] {
            border: 1px solid rgba(112,151,190,.12) !important;
            border-radius: 10px !important;
            padding: .42rem !important;
        }
        [data-testid="stFileUploaderDropzone"] {
            background: rgba(9,23,38,.52) !important;
            border-color: rgba(112,151,190,.14) !important;
            border-radius: 9px !important;
        }

        /* ---------- Buttons ---------- */
        div[data-testid="stButton"] button,
        div[data-testid="stDownloadButton"] button {
            min-height: 2.42rem !important;
            border-radius: 9px !important;
            font-size: .91rem !important;
            font-weight: 680 !important;
            letter-spacing: -.01em !important;
            box-shadow: none !important;
            transition:
                border-color .14s ease,
                background .14s ease,
                color .14s ease !important;
        }

        div[data-testid="stButton"] button[kind="primary"],
        div[data-testid="stDownloadButton"] button[kind="primary"] {
            color: #f2fbf6 !important;
            border: 1px solid rgba(67,209,125,.35) !important;
            background:
                linear-gradient(145deg, rgba(34,130,73,.82), rgba(20,88,52,.86)) !important;
            box-shadow: inset 0 1px 0 rgba(255,255,255,.045) !important;
        }
        div[data-testid="stButton"] button[kind="primary"]:hover:not(:disabled),
        div[data-testid="stDownloadButton"] button[kind="primary"]:hover:not(:disabled) {
            border-color: rgba(86,222,142,.52) !important;
            background:
                linear-gradient(145deg, rgba(39,145,81,.88), rgba(22,98,57,.90)) !important;
            transform: none !important;
        }

        div[data-testid="stButton"] button[kind="secondary"],
        div[data-testid="stDownloadButton"] button[kind="secondary"] {
            color: #dce6f0 !important;
            border: 1px solid rgba(112,151,190,.15) !important;
            background: rgba(10,25,42,.46) !important;
        }
        div[data-testid="stButton"] button[kind="secondary"]:hover:not(:disabled),
        div[data-testid="stDownloadButton"] button[kind="secondary"]:hover:not(:disabled) {
            color: #eef4fa !important;
            border-color: rgba(112,165,211,.25) !important;
            background: rgba(16,35,55,.56) !important;
        }

        div[data-testid="stButton"] button:disabled,
        div[data-testid="stDownloadButton"] button:disabled {
            opacity: .62 !important;
            color: #76899f !important;
            background: rgba(11,25,40,.48) !important;
            border-color: rgba(112,151,190,.08) !important;
            box-shadow: none !important;
        }

        /* ---------- Alerts / status ---------- */
        div[data-testid="stAlert"] {
            border-radius: 10px !important;
            border: 1px solid rgba(112,151,190,.12) !important;
            background: rgba(10,24,40,.56) !important;
            box-shadow: none !important;
        }
        [data-testid="stStatusWidget"] {
            border: 1px solid rgba(112,151,190,.13) !important;
            border-radius: 10px !important;
            overflow: hidden !important;
            box-shadow: none !important;
        }

        /* ---------- Progress ---------- */
        [data-testid="stProgress"] {
            color: #dce6ef !important;
            margin-top: .25rem !important;
        }
        [data-testid="stProgress"] > div {
            background: transparent !important;
        }
        [data-testid="stProgress"] > div > div {
            color: #dce6ef !important;
            background: rgba(8,21,35,.58) !important;
            box-shadow: none !important;
        }
        [data-testid="stProgress"] p,
        [data-testid="stProgress"] span {
            color: #dce6ef !important;
            background: transparent !important;
            text-shadow: none !important;
        }
        [data-testid="stProgress"] [role="progressbar"] {
            min-height: 8px !important;
            overflow: hidden !important;
            border: 1px solid rgba(112,151,190,.12) !important;
            border-radius: 999px !important;
            background: rgba(17,38,61,.48) !important;
            box-shadow: inset 0 1px 2px rgba(0,0,0,.18) !important;
        }
        [data-testid="stProgress"] [role="progressbar"] > div {
            border-radius: 999px !important;
            background:
                linear-gradient(90deg, #2c8656 0%, #3eaf70 100%) !important;
            box-shadow: none !important;
        }

        /* ---------- Checkboxes / toggles ---------- */
        [data-baseweb="checkbox"] [aria-checked="true"],
        [data-testid="stToggle"] [aria-checked="true"] {
            background-color: #35b86b !important;
            border-color: #35b86b !important;
        }

        /* ---------- Popovers / menus ---------- */
        [data-baseweb="popover"],
        [data-baseweb="menu"] {
            background: rgba(7,18,31,.985) !important;
            border-color: rgba(112,151,190,.15) !important;
            box-shadow: var(--til-shadow) !important;
        }

        /* ---------- Sharp / energetic terminal pass ---------- */
        /* This layer intentionally adds more contrast and energy than the
           restrained base theme while keeping the same data and app logic. */
        html,
        body,
        [data-testid="stAppViewContainer"],
        .stApp {
            background:
                radial-gradient(circle at 82% -8%, rgba(37, 166, 238, .12), transparent 31%),
                radial-gradient(circle at 7% 20%, rgba(40, 215, 127, .055), transparent 25%),
                linear-gradient(180deg, #030a12 0%, #06111d 45%, #040d17 100%) !important;
        }

        [data-testid="stHeader"] {
            border-bottom: 1px solid rgba(91,199,255,.12) !important;
            box-shadow: 0 8px 26px rgba(0,0,0,.13) !important;
        }

        .til-hero {
            border-color: rgba(91,199,255,.18) !important;
            border-radius: 9px !important;
            background:
                linear-gradient(110deg, rgba(9,29,48,.88), rgba(5,16,28,.72)) !important;
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,.028),
                0 0 0 1px rgba(31,109,154,.04),
                0 10px 34px rgba(0,0,0,.15) !important;
        }
        .til-hero::after {
            width: 3px !important;
            background:
                linear-gradient(180deg, #52d8ff 0%, #3be27e 100%) !important;
            box-shadow: 0 0 14px rgba(82,216,255,.24) !important;
        }
        .til-brand-mark,
        .til-sidebrand-mark {
            color: #61e69b !important;
            border-color: rgba(80,225,149,.35) !important;
            background:
                linear-gradient(145deg, rgba(42,170,103,.16), rgba(18,96,147,.10)) !important;
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,.045),
                0 0 16px rgba(48,207,126,.08) !important;
        }
        .til-title {
            color: #f8fbff !important;
        }

        .til-pagehead {
            border-color: rgba(91,199,255,.24) !important;
            border-radius: 9px !important;
            background:
                linear-gradient(110deg, rgba(8,30,50,.96), rgba(5,17,29,.84) 72%),
                radial-gradient(circle at 94% 15%, rgba(52,217,134,.16), transparent 32%) !important;
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,.035),
                0 0 0 1px rgba(91,199,255,.025),
                0 16px 38px rgba(0,0,0,.17) !important;
        }
        .til-pagehead::before {
            width: 3px !important;
            top: 0 !important;
            bottom: 0 !important;
            border-radius: 0 !important;
            background:
                linear-gradient(180deg, #57d5ff 0%, #38e17d 100%) !important;
            box-shadow:
                0 0 18px rgba(82,216,255,.28),
                0 0 24px rgba(56,225,125,.12) !important;
        }
        .til-pagehead::after {
            content: "";
            position: absolute;
            right: 0;
            top: 0;
            width: 220px;
            height: 2px;
            background:
                linear-gradient(90deg, transparent, rgba(82,216,255,.58), rgba(60,226,129,.45)) !important;
        }
        .til-page-eyebrow {
            color: #77bad9 !important;
            font-weight: 900 !important;
        }
        .til-page-eyebrow span {
            color: #4de08a !important;
        }
        .til-page-title {
            color: #f8fbff !important;
            font-size: 2.10rem !important;
            font-weight: 850 !important;
            text-shadow: 0 0 24px rgba(98,201,255,.055) !important;
        }
        .til-page-sub {
            color: #a6b9cc !important;
        }
        .til-page-step {
            color: rgba(92,201,255,.11) !important;
            text-shadow: 0 0 28px rgba(73,210,255,.05) !important;
        }

        /* Sidebar: more product-like and less default Streamlit. */
        [data-testid="stSidebar"] {
            background:
                radial-gradient(circle at 25% 1%, rgba(31,115,170,.09), transparent 24%),
                linear-gradient(180deg, #04101b 0%, #030b13 100%) !important;
            border-right: 1px solid rgba(91,199,255,.17) !important;
            box-shadow: 10px 0 34px rgba(0,0,0,.12) !important;
        }
        .til-sidebrand {
            padding-bottom: 12px !important;
        }
        .til-sidebrand-name {
            color: #f7fbff !important;
            font-size: .95rem !important;
        }
        .til-sidebrand-sub {
            color: #78a4c3 !important;
        }
        .til-sideflow {
            border-color: rgba(91,199,255,.13) !important;
            border-radius: 7px !important;
            color: #7da2bd !important;
            background:
                linear-gradient(90deg, rgba(16,56,84,.25), rgba(11,29,46,.20)) !important;
        }

        .st-key-til_workspace_section div[role="radiogroup"] {
            gap: .14rem !important;
        }
        .st-key-til_workspace_section div[role="radiogroup"] > label {
            min-height: 2.38rem !important;
            border-radius: 7px !important;
            padding-left: .70rem !important;
        }
        .st-key-til_workspace_section div[role="radiogroup"] > label:hover {
            background:
                linear-gradient(90deg, rgba(28,92,130,.28), rgba(13,38,59,.18)) !important;
            border-color: rgba(91,199,255,.18) !important;
        }
        .st-key-til_workspace_section div[role="radiogroup"] > label:has(input:checked) {
            background:
                linear-gradient(90deg, rgba(24,99,116,.44), rgba(20,71,58,.25)) !important;
            border-color: rgba(75,221,139,.34) !important;
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,.035),
                0 0 18px rgba(55,216,128,.07) !important;
        }
        .st-key-til_workspace_section div[role="radiogroup"] > label:has(input:checked)::after {
            width: 3px !important;
            top: 5px !important;
            bottom: 5px !important;
            background:
                linear-gradient(180deg, #55d7ff, #3de483) !important;
            box-shadow:
                0 0 10px rgba(71,224,139,.28) !important;
        }
        .st-key-til_workspace_section div[role="radiogroup"] > label p {
            color: #afc1d3 !important;
            font-weight: 650 !important;
        }
        .st-key-til_workspace_section div[role="radiogroup"] > label:has(input:checked) p {
            color: #f8fbff !important;
            font-weight: 780 !important;
        }

        /* BaseWeb can nest the native radio marker several levels deep. */
        .st-key-til_workspace_section input[type="radio"],
        .st-key-til_workspace_section [data-baseweb="radio"] > div:first-child,
        .st-key-til_workspace_section label div:has(> input[type="radio"]),
        .st-key-til_workspace_section label span:has(> input[type="radio"]),
        .st-key-til_workspace_section [data-baseweb="radio"] div:has(input[type="radio"]) {
            position: absolute !important;
            opacity: 0 !important;
            width: 1px !important;
            height: 1px !important;
            min-width: 1px !important;
            min-height: 1px !important;
            overflow: hidden !important;
            pointer-events: none !important;
            margin: 0 !important;
            padding: 0 !important;
        }

        /* Tone the native Streamlit multipage navigation into a compact tool rail. */
        [data-testid="stSidebarNav"] {
            padding-bottom: .75rem !important;
            margin-bottom: .75rem !important;
            border-bottom: 1px solid rgba(91,199,255,.14) !important;
        }
        [data-testid="stSidebarNav"] a {
            border-radius: 6px !important;
            color: #9cb0c5 !important;
            transition: background .14s ease, color .14s ease !important;
        }
        [data-testid="stSidebarNav"] a:hover {
            color: #ecf8ff !important;
            background: rgba(27,86,122,.26) !important;
        }
        [data-testid="stSidebarNav"] a[aria-current="page"] {
            color: #f6fbff !important;
            background:
                linear-gradient(90deg, rgba(36,104,145,.54), rgba(23,56,78,.32)) !important;
            box-shadow: inset 2px 0 0 #55d5ff !important;
        }

        /* Sharper surfaces with subtle cyan energy. */
        .til-card,
        [data-testid="stExpander"] details,
        div[data-testid="stDataFrame"],
        div[data-testid="stTable"],
        [data-testid="stFileUploader"],
        [data-testid="stStatusWidget"],
        [data-testid="stForm"] {
            border-radius: 8px !important;
            border-color: rgba(91,199,255,.17) !important;
            background:
                linear-gradient(145deg, rgba(8,28,46,.80), rgba(5,17,29,.70)) !important;
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,.025),
                0 11px 28px rgba(0,0,0,.12) !important;
        }
        .til-card {
            position: relative !important;
            overflow: hidden !important;
        }
        .til-card::before {
            content: "";
            position: absolute;
            top: 0;
            left: 16px;
            right: 52%;
            height: 1px;
            background:
                linear-gradient(90deg, #55d4ff, rgba(55,224,131,.30), transparent) !important;
        }
        .til-card:hover {
            border-color: rgba(91,199,255,.30) !important;
            background:
                linear-gradient(145deg, rgba(10,35,57,.88), rgba(5,18,30,.74)) !important;
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,.035),
                0 14px 34px rgba(0,0,0,.14) !important;
        }

        [data-testid="stMetric"] {
            position: relative !important;
            overflow: hidden !important;
            min-height: 108px !important;
            border-radius: 8px !important;
            border-color: rgba(91,199,255,.19) !important;
            background:
                linear-gradient(145deg, rgba(10,33,53,.75), rgba(6,19,31,.63)) !important;
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,.026),
                0 9px 26px rgba(0,0,0,.10) !important;
        }
        [data-testid="stMetric"]::before {
            content: "";
            position: absolute;
            left: 0;
            top: 0;
            width: 36%;
            height: 2px;
            background:
                linear-gradient(90deg, #54d5ff, #42df86, transparent) !important;
        }
        [data-testid="stMetricLabel"] {
            color: #8fb0c8 !important;
            font-weight: 720 !important;
        }
        [data-testid="stMetricValue"] {
            color: #f7fbff !important;
            font-weight: 790 !important;
        }

        /* Inputs should feel crisp and responsive. */
        div[data-baseweb="select"] > div,
        [data-testid="stTextInput"] input,
        [data-testid="stNumberInput"] input,
        [data-testid="stTextArea"] textarea {
            border-radius: 7px !important;
            border-color: rgba(91,199,255,.20) !important;
            background:
                linear-gradient(180deg, rgba(9,30,49,.86), rgba(7,22,37,.88)) !important;
        }
        div[data-baseweb="select"] > div:hover,
        [data-testid="stTextInput"] input:hover,
        [data-testid="stTextArea"] textarea:hover {
            border-color: rgba(91,199,255,.42) !important;
        }
        div[data-baseweb="select"] > div:focus-within,
        [data-testid="stTextInput"] input:focus,
        [data-testid="stNumberInput"] input:focus,
        [data-testid="stTextArea"] textarea:focus {
            border-color: #54ceff !important;
            box-shadow:
                0 0 0 2px rgba(84,206,255,.08),
                0 0 18px rgba(61,194,255,.07) !important;
        }
        [data-testid="stFileUploaderDropzone"] {
            border-radius: 7px !important;
            border: 1px dashed rgba(91,199,255,.28) !important;
            background:
                linear-gradient(135deg, rgba(9,32,53,.66), rgba(7,22,37,.48)) !important;
        }

        /* Primary actions: darker, higher-contrast teal so labels stay readable. */
        div[data-testid="stButton"] button,
        div[data-testid="stDownloadButton"] button {
            border-radius: 7px !important;
            font-weight: 740 !important;
        }
        div[data-testid="stButton"] button[kind="primary"],
        div[data-testid="stDownloadButton"] button[kind="primary"] {
            color: #f4fbff !important;
            border-color: rgba(70,174,167,.58) !important;
            background:
                linear-gradient(100deg, #176a67 0%, #1d7770 58%, #19666f 100%) !important;
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,.08),
                0 0 16px rgba(42,164,157,.08) !important;
        }
        div[data-testid="stButton"] button[kind="primary"]:hover:not(:disabled),
        div[data-testid="stDownloadButton"] button[kind="primary"]:hover:not(:disabled) {
            color: #ffffff !important;
            border-color: rgba(91,207,198,.72) !important;
            background:
                linear-gradient(100deg, #1d7b76 0%, #23877e 58%, #1d7480 100%) !important;
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,.11),
                0 0 18px rgba(58,187,179,.11) !important;
        }
        div[data-testid="stButton"] button[kind="secondary"],
        div[data-testid="stDownloadButton"] button[kind="secondary"] {
            border-color: rgba(91,199,255,.22) !important;
            background:
                linear-gradient(180deg, rgba(13,40,64,.72), rgba(8,25,42,.70)) !important;
        }
        div[data-testid="stButton"] button[kind="secondary"]:hover:not(:disabled),
        div[data-testid="stDownloadButton"] button[kind="secondary"]:hover:not(:disabled) {
            border-color: rgba(91,199,255,.46) !important;
            background:
                linear-gradient(180deg, rgba(17,52,82,.82), rgba(10,31,51,.78)) !important;
            box-shadow: 0 0 18px rgba(75,195,255,.06) !important;
        }

        [data-baseweb="tab-list"] {
            border-bottom-color: rgba(91,199,255,.17) !important;
        }
        [data-baseweb="tab"] {
            border-radius: 6px 6px 0 0 !important;
        }
        [aria-selected="true"][data-baseweb="tab"] {
            color: #f7fbff !important;
            background:
                linear-gradient(180deg, rgba(30,105,145,.24), rgba(13,39,61,.30)) !important;
        }
        [data-baseweb="tab-highlight"] {
            background:
                linear-gradient(90deg, #54d5ff, #3fe283) !important;
            box-shadow: 0 0 10px rgba(74,213,255,.18) !important;
        }

        /* Success/status should be rich, not a flat green slab. */
        div[data-testid="stAlert"] {
            border-radius: 8px !important;
            border-color: rgba(91,199,255,.17) !important;
            background:
                linear-gradient(110deg, rgba(9,32,49,.76), rgba(7,23,37,.66)) !important;
        }
        div[data-testid="stAlert"]:has([data-testid="stAlertContentSuccess"]),
        div[data-testid="stAlert"][data-baseweb="notification"][kind="positive"] {
            border-color: rgba(68,224,132,.28) !important;
            background:
                linear-gradient(110deg, rgba(14,67,47,.68), rgba(7,33,31,.62)) !important;
        }

        [data-testid="stProgress"] [role="progressbar"] {
            min-height: 10px !important;
            border-color: rgba(104,154,194,.34) !important;
            background: #081521 !important;
            box-shadow:
                inset 0 0 0 1px rgba(0,0,0,.32),
                inset 0 1px 3px rgba(0,0,0,.42) !important;
        }
        [data-testid="stProgress"] [role="progressbar"] > div {
            background: #3f9fca !important;
            border-right: 2px solid rgba(228,246,255,.82) !important;
            box-shadow: 0 0 8px rgba(63,159,202,.18) !important;
        }

        /* ---------- Concept-match layout overrides ---------- */
        /* The app uses Streamlit's native locked sidebar state on desktop, so
           the Cloud header can stay hidden without trapping navigation. */
        [data-testid="stHeader"] {
            height: 0 !important;
            min-height: 0 !important;
            background: transparent !important;
            border: 0 !important;
            box-shadow: none !important;
        }
        [data-testid="stToolbar"],
        [data-testid="stAppDeployButton"],
        [data-testid="stDecoration"],
        #MainMenu {
            display: none !important;
            visibility: hidden !important;
        }
        .stApp > header {
            height: 0 !important;
            min-height: 0 !important;
        }

        /* Match the mockup proportions more closely. */
        [data-testid="stSidebar"] {
            width: 294px !important;
            min-width: 294px !important;
            max-width: 294px !important;
        }
        /* Defensive fallback for stale browser sidebar state during hot deploys.
           Native page config locks the sidebar open, but if Streamlit briefly
           renders aria-expanded=false, do not allow its collapsed transform
           or zero-width styles to hide the app's primary navigation rail. */
        [data-testid="stSidebar"][aria-expanded="false"] {
            transform: none !important;
            width: 294px !important;
            min-width: 294px !important;
            max-width: 294px !important;
            visibility: visible !important;
        }
        [data-testid="stSidebar"] > div:first-child {
            width: 294px !important;
        }
        [data-testid="stSidebar"] .block-container {
            padding: 1.35rem 1.08rem 1rem !important;
        }
        .block-container {
            max-width: 1640px !important;
            padding-top: .85rem !important;
            padding-left: 1.75rem !important;
            padding-right: 1.6rem !important;
            padding-bottom: 2.4rem !important;
        }

        /* Larger crystalline logo / brand, matching the concept. */
        .til-sidebrand {
            position: relative;
            align-items: flex-start !important;
            gap: 13px !important;
            padding: 0 4px 17px !important;
            margin-bottom: 6px !important;
            border-bottom: 1px solid rgba(75,184,229,.11);
        }
        .til-crystal-logo {
            flex: 0 0 58px !important;
            width: 58px !important;
            height: 64px !important;
            border-radius: 0 !important;
            background: transparent !important;
            box-shadow: none !important;
        }
        .til-crystal-logo svg {
            width: 58px;
            height: 64px;
            overflow: visible;
            filter:
                drop-shadow(0 0 8px rgba(72,214,255,.16))
                drop-shadow(0 0 12px rgba(61,230,159,.07));
        }
        .til-sidebrand-name {
            margin-top: 7px !important;
            color: #f7fbff !important;
            font-size: .91rem !important;
            line-height: 1.14 !important;
            font-weight: 820 !important;
            letter-spacing: .055em !important;
        }
        .til-side-collapse {
            position: absolute;
            right: 2px;
            top: 5px;
            color: #65bce8;
            font-size: 1.04rem;
            font-weight: 700;
            letter-spacing: -.18em;
            opacity: .88;
        }
        .til-workspace-label {
            margin: 8px 5px 7px;
            color: #3fd1fa;
            font-size: .61rem;
            font-weight: 900;
            letter-spacing: .17em;
        }

        /* Dashboard home tile above workflow groups. */
        .st-key-til_dashboard,
        .st-key-til_dashboard_active {
            margin: 0 0 .80rem !important;
        }
        [data-testid="stSidebar"] .st-key-til_dashboard div[data-testid="stButton"] button[kind="secondary"],
        [data-testid="stSidebar"] .st-key-til_dashboard_active div[data-testid="stButton"] button[kind="secondary"] {
            position: relative !important;
            min-height: 55px !important;
            justify-content: flex-start !important;
            padding: .48rem .68rem .48rem .82rem !important;
            border: 1px solid rgba(67,187,239,.18) !important;
            border-radius: 8px !important;
            color: #d9ecf8 !important;
            background:
                linear-gradient(100deg, rgba(9,36,57,.88), rgba(7,24,39,.72)) !important;
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,.025),
                0 8px 22px rgba(0,0,0,.10) !important;
            white-space: pre-line !important;
        }
        [data-testid="stSidebar"] .st-key-til_dashboard_active div[data-testid="stButton"] button[kind="secondary"] {
            border-color: rgba(72,210,255,.32) !important;
            background:
                linear-gradient(90deg, rgba(12,69,96,.72), rgba(8,35,55,.80)) !important;
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,.04),
                0 0 18px rgba(55,205,255,.07) !important;
        }
        [data-testid="stSidebar"] .st-key-til_dashboard_active div[data-testid="stButton"] button[kind="secondary"]::after {
            content: "›";
            position: absolute;
            right: 11px;
            top: 50%;
            transform: translateY(-50%);
            color: #4ed7ff;
            font-size: 1.2rem;
            text-shadow: 0 0 10px rgba(78,215,255,.30);
        }
        [data-testid="stSidebar"] .st-key-til_dashboard div[data-testid="stButton"] button p,
        [data-testid="stSidebar"] .st-key-til_dashboard_active div[data-testid="stButton"] button p {
            width: 100% !important;
            text-align: left !important;
            font-size: .76rem !important;
            line-height: 1.34 !important;
            font-weight: 760 !important;
            color: inherit !important;
        }

        /* Sidebar workflow rows should read like a terminal nav, not cards. */
        .til-nav-group {
            margin: .90rem .32rem .38rem !important;
            color: #399bc8 !important;
            font-size: .58rem !important;
            letter-spacing: .18em !important;
        }
        [data-testid="stSidebar"] [class*="st-key-til_nav_"] div[data-testid="stButton"] button[kind="secondary"] {
            min-height: 2.05rem !important;
            padding-top: .22rem !important;
            padding-bottom: .22rem !important;
            border-radius: 6px !important;
            font-size: .77rem !important;
            color: #aebfd0 !important;
        }
        [data-testid="stSidebar"] [class*="st-key-til_nav_active_"] div[data-testid="stButton"] button[kind="secondary"] {
            color: #fbfdff !important;
            border-color: rgba(64,226,149,.42) !important;
            background:
                linear-gradient(90deg, rgba(10,100,114,.74), rgba(10,57,50,.42) 72%, rgba(6,22,34,.24)) !important;
        }

        /* Utility bar sits where the mockup's search / icons live. */
        .til-top-actions {
            display: flex;
            align-items: center;
            justify-content: flex-end;
            gap: 10px;
            min-height: 38px;
        }
        .til-top-icon {
            position: relative;
            display: flex;
            align-items: center;
            justify-content: center;
            width: 28px;
            height: 28px;
            color: #d8ebf6;
            border: 1px solid rgba(76,188,235,.14);
            border-radius: 50%;
            background: rgba(7,24,39,.52);
            font-size: .74rem;
            font-weight: 850;
        }
        .til-notify span {
            position: absolute;
            right: 0;
            top: -1px;
            width: 6px;
            height: 6px;
            border: 1px solid #06101a;
            border-radius: 50%;
            background: #4bf091;
            box-shadow: 0 0 8px rgba(75,240,145,.55);
        }
        .til-avatar {
            display: flex;
            align-items: center;
            justify-content: center;
            width: 31px;
            height: 31px;
            border: 1px solid rgba(62,230,159,.48);
            border-radius: 50%;
            color: #dffcf1;
            background:
                radial-gradient(circle, rgba(23,117,91,.42), rgba(8,33,43,.68));
            box-shadow: 0 0 14px rgba(56,226,158,.10);
            font-size: .68rem;
            font-weight: 850;
        }
        .til-top-chevron {
            color: #66a5c6;
            font-size: .84rem;
        }

        /* Make header mesh denser and more like the rendered concept. */
        .til-pagehead {
            min-height: 148px !important;
            padding: 24px 24px 21px !important;
            border-radius: 0 !important;
            border-left: 0 !important;
            border-right: 0 !important;
            border-top-color: rgba(68,192,234,.12) !important;
            border-bottom-color: rgba(65,223,194,.18) !important;
            background:
                linear-gradient(90deg, rgba(4,19,31,.98), rgba(5,29,43,.86) 72%, rgba(5,65,64,.42)) !important;
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,.02),
                0 10px 34px rgba(0,0,0,.13) !important;
        }
        .til-pagehead::before {
            left: -1px !important;
            width: 2px !important;
            background: linear-gradient(180deg, #48d9ff, #43eca0) !important;
        }
        .til-pagehead::after {
            width: 45% !important;
            height: 1px !important;
            background: linear-gradient(90deg, transparent, rgba(69,213,255,.36), rgba(62,230,158,.44)) !important;
        }
        .til-page-title {
            font-size: 2.12rem !important;
            margin-top: 3px !important;
        }
        .til-page-sub {
            max-width: 760px !important;
            font-size: .88rem !important;
        }
        .til-market-mesh {
            right: 0 !important;
            bottom: -2px !important;
            width: 54% !important;
            opacity: 1 !important;
        }
        .til-market-mesh svg {
            filter:
                drop-shadow(0 0 4px rgba(73,212,255,.15))
                drop-shadow(0 0 10px rgba(57,226,161,.06)) !important;
        }
        .til-page-step {
            position: absolute !important;
            z-index: 2 !important;
            right: 20px !important;
            top: 36px !important;
            color: rgba(39,157,174,.18) !important;
            font-size: 5.5rem !important;
        }

        /* KPI cards more compact and icon-heavy, like the concept. */
        .til-kpi-grid {
            gap: 17px !important;
            margin-top: 18px !important;
            margin-bottom: 16px !important;
        }
        .til-kpi {
            min-height: 105px !important;
            padding: 15px 17px !important;
            border-radius: 8px !important;
            border-color: rgba(72,175,221,.18) !important;
            background:
                linear-gradient(145deg, rgba(8,27,43,.94), rgba(6,18,30,.90)) !important;
        }
        .til-kpi-icon {
            flex-basis: 48px !important;
            width: 48px !important;
            height: 48px !important;
            font-size: 1.18rem !important;
        }
        .til-kpi-label {
            font-size: .53rem !important;
        }
        .til-kpi-value {
            font-size: 1.70rem !important;
        }

        /* ---------- Top utility rail ---------- */
        .til-top-status {
            display: inline-flex;
            align-items: center;
            gap: 7px;
            min-height: 38px;
            padding: 0 3px;
            color: #6688a0;
            font-size: .58rem;
            font-weight: 850;
            letter-spacing: .12em;
        }
        .til-top-status strong {
            color: #53e49a;
            font-weight: 900;
        }
        .til-top-status-dot {
            width: 6px;
            height: 6px;
            border-radius: 999px;
            background: #4ce795;
            box-shadow: 0 0 10px rgba(76,231,149,.68);
        }
        .st-key-til_workspace_search {
            margin-bottom: .25rem !important;
        }
        .st-key-til_workspace_search input {
            min-height: 38px !important;
            border: 1px solid rgba(79,194,244,.24) !important;
            border-radius: 7px !important;
            color: #dff5ff !important;
            background:
                linear-gradient(180deg, rgba(7,25,42,.92), rgba(5,17,29,.92)) !important;
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,.02),
                0 0 18px rgba(57,189,245,.03) !important;
        }
        .st-key-til_workspace_search input:focus {
            border-color: rgba(82,216,255,.58) !important;
            box-shadow:
                0 0 0 2px rgba(82,216,255,.07),
                0 0 20px rgba(58,204,255,.08) !important;
        }
        .st-key-til_workspace_search input::placeholder {
            color: #67879d !important;
        }

        /* ---------- Trading Intelligence premium component system ---------- */

        /* Replace Streamlit's multipage list with our in-app app switcher. */
        [data-testid="stSidebarNav"] {
            display: none !important;
        }

        /* Animated geometric product mark. */
        .til-sidebrand-mark {
            position: relative !important;
            overflow: visible !important;
            border: 0 !important;
            background:
                radial-gradient(circle at 50% 50%, rgba(64,231,168,.16), rgba(31,167,236,.09) 52%, transparent 54%) !important;
            box-shadow: 0 0 24px rgba(55,213,188,.12) !important;
        }
        .til-logo-core {
            position: absolute;
            left: 50%;
            top: 50%;
            width: 10px;
            height: 10px;
            transform: translate(-50%, -50%) rotate(45deg);
            border: 1px solid #65eab0;
            background: rgba(37,177,211,.20);
            box-shadow: 0 0 10px rgba(82,223,185,.42);
        }
        .til-logo-orbit {
            position: absolute;
            left: 50%;
            top: 50%;
            width: 26px;
            height: 12px;
            border: 1px solid rgba(78,211,255,.58);
            border-radius: 50%;
            transform-origin: center;
        }
        .til-logo-orbit-a {
            transform: translate(-50%, -50%) rotate(35deg);
        }
        .til-logo-orbit-b {
            transform: translate(-50%, -50%) rotate(-35deg);
            border-color: rgba(68,228,143,.52);
        }
        .til-sidebrand-name {
            text-transform: uppercase;
            font-size: .90rem !important;
            letter-spacing: .045em !important;
            line-height: 1.16 !important;
        }
        .til-sidebrand-sub {
            text-transform: uppercase;
            letter-spacing: .09em !important;
            color: #6c9dbb !important;
        }
        .til-sideflow span {
            color: #57e6aa;
            text-shadow: 0 0 10px rgba(87,230,170,.26);
        }

        /* Custom sidebar navigation buttons: no radios, no circles. */
        .til-nav-group {
            margin: 1.02rem .32rem .42rem;
            color: #5f91b1;
            font-size: .61rem;
            font-weight: 900;
            letter-spacing: .17em;
            text-transform: uppercase;
        }
        [class*="st-key-til_nav_"] {
            margin: 0 0 3px !important;
        }
        [data-testid="stSidebar"] [class*="st-key-til_nav_"] div[data-testid="stButton"] button[kind="secondary"] {
            position: relative !important;
            justify-content: flex-start !important;
            min-height: 2.34rem !important;
            padding: .34rem .65rem .34rem .72rem !important;
            border-radius: 7px !important;
            border: 1px solid transparent !important;
            color: #a9bed1 !important;
            background: transparent !important;
            box-shadow: none !important;
            font-size: .89rem !important;
            font-weight: 670 !important;
            letter-spacing: -.012em !important;
            text-align: left !important;
            overflow: hidden !important;
        }
        [data-testid="stSidebar"] [class*="st-key-til_nav_"] div[data-testid="stButton"] button[kind="secondary"] p {
            width: 100% !important;
            text-align: left !important;
            color: inherit !important;
            white-space: nowrap !important;
        }
        [data-testid="stSidebar"] [class*="st-key-til_nav_"] div[data-testid="stButton"] button[kind="secondary"]:hover {
            color: #eef8ff !important;
            border-color: rgba(75,201,255,.18) !important;
            background:
                linear-gradient(90deg, rgba(27,92,132,.30), rgba(12,35,55,.12)) !important;
        }
        [data-testid="stSidebar"] [class*="st-key-til_nav_active_"] div[data-testid="stButton"] button[kind="secondary"] {
            color: #fbfdff !important;
            padding-left: 1.18rem !important;
            border-color: rgba(69,226,145,.40) !important;
            background:
                linear-gradient(90deg, rgba(11,102,115,.62), rgba(12,69,55,.34) 68%, rgba(12,31,47,.20)) !important;
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,.05),
                0 0 22px rgba(47,222,146,.08) !important;
        }
        [data-testid="stSidebar"] [class*="st-key-til_nav_active_"] div[data-testid="stButton"] button[kind="secondary"]::before {
            content: "";
            position: absolute;
            left: 8px;
            top: 50%;
            width: 8px;
            height: 8px;
            transform: translateY(-50%) rotate(45deg);
            border-radius: 1px;
            background:
                linear-gradient(135deg, #5addff 0%, #49eca0 100%);
            box-shadow:
                0 0 8px rgba(91,221,255,.48),
                0 0 14px rgba(73,236,160,.28);
        }
        [data-testid="stSidebar"] [class*="st-key-til_nav_active_"] div[data-testid="stButton"] button[kind="secondary"]::after {
            content: "";
            position: absolute;
            left: 0;
            top: 5px;
            bottom: 5px;
            width: 3px;
            border-radius: 999px;
            background:
                linear-gradient(180deg, #57d9ff 0%, #42e88d 100%);
            box-shadow: 0 0 12px rgba(72,225,174,.38);
        }

        .til-side-labs-note {
            margin: .45rem .3rem 0;
            color: #4f7087;
            font-size: .56rem;
            font-weight: 700;
            letter-spacing: .05em;
            text-transform: uppercase;
        }

        .til-side-status {
            margin: 1.05rem .15rem .55rem;
            padding: 10px 11px;
            border: 1px solid rgba(61,219,154,.18);
            border-radius: 8px;
            background:
                linear-gradient(120deg, rgba(10,42,48,.72), rgba(5,24,38,.72));
            box-shadow: inset 0 1px 0 rgba(255,255,255,.025);
        }
        .til-side-status-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
        }
        .til-side-status-label {
            color: #6e91a8;
            font-size: .54rem;
            line-height: 1;
            font-weight: 900;
            letter-spacing: .13em;
        }
        .til-side-status-value {
            margin-top: 5px;
            color: #64e99e;
            font-size: .68rem;
            font-weight: 850;
            letter-spacing: .08em;
        }
        .til-live-dot {
            display: inline-block;
            width: 6px;
            height: 6px;
            margin-right: 5px;
            border-radius: 999px;
            background: #45e98c;
            box-shadow: 0 0 10px rgba(69,233,140,.65);
        }
        .til-mini-spark {
            width: 78px;
            height: 26px;
            opacity: .95;
        }

        /* Header data-mesh graphic. */
        .til-pagehead-main {
            position: relative;
            z-index: 3;
        }
        .til-market-mesh {
            position: absolute;
            z-index: 1;
            right: 34px;
            bottom: 0;
            width: 48%;
            height: 100%;
            opacity: .83;
            pointer-events: none;
            mask-image: linear-gradient(90deg, transparent 0%, rgba(0,0,0,.32) 18%, #000 55%, #000 100%);
            -webkit-mask-image: linear-gradient(90deg, transparent 0%, rgba(0,0,0,.32) 18%, #000 55%, #000 100%);
        }
        .til-market-mesh svg {
            width: 100%;
            height: 100%;
            overflow: visible;
            filter: drop-shadow(0 0 6px rgba(58,204,255,.10));
        }
        .til-page-step {
            position: relative;
            z-index: 2;
        }

        /* Real data KPI cards. */
        .til-kpi-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 14px;
            margin: 0 0 15px;
        }
        .til-kpi {
            position: relative;
            display: flex;
            align-items: center;
            min-height: 102px;
            gap: 14px;
            overflow: hidden;
            padding: 15px 16px;
            border: 1px solid rgba(89,190,244,.19);
            border-radius: 9px;
            background:
                linear-gradient(145deg, rgba(8,29,48,.90), rgba(6,18,31,.82));
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,.025),
                0 10px 28px rgba(0,0,0,.12);
        }
        .til-kpi::after {
            content: "";
            position: absolute;
            left: 0;
            right: 42%;
            top: 0;
            height: 1px;
            background: linear-gradient(90deg, var(--kpi-accent), transparent);
            opacity: .78;
        }
        .til-kpi-icon {
            display: flex;
            align-items: center;
            justify-content: center;
            flex: 0 0 52px;
            width: 52px;
            height: 52px;
            border-radius: 50%;
            color: var(--kpi-accent);
            border: 1px solid color-mix(in srgb, var(--kpi-accent) 35%, transparent);
            background:
                radial-gradient(circle, color-mix(in srgb, var(--kpi-accent) 17%, transparent), transparent 70%);
            box-shadow: 0 0 24px color-mix(in srgb, var(--kpi-accent) 12%, transparent);
            font-size: 1.35rem;
            font-weight: 800;
        }
        .til-kpi-cyan { --kpi-accent: #43d9ff; }
        .til-kpi-blue { --kpi-accent: #54a9ff; }
        .til-kpi-green { --kpi-accent: #54ea91; }
        .til-kpi-purple { --kpi-accent: #b66aff; }
        .til-kpi-label {
            color: #8ea7bc;
            font-size: .58rem;
            line-height: 1;
            font-weight: 900;
            letter-spacing: .14em;
        }
        .til-kpi-value {
            margin-top: 7px;
            color: #f7fbff;
            font-size: 1.78rem;
            line-height: 1;
            font-weight: 860;
            letter-spacing: -.04em;
        }
        .til-kpi-note {
            margin-top: 7px;
            color: var(--kpi-accent);
            font-size: .68rem;
            font-weight: 650;
            opacity: .86;
        }

        /* Synced storage strip. */
        .til-sync-banner {
            display: flex;
            align-items: center;
            gap: 12px;
            min-height: 58px;
            margin: 0 0 16px;
            padding: 10px 12px;
            border: 1px solid rgba(72,201,255,.30);
            border-radius: 8px;
            background:
                linear-gradient(100deg, rgba(5,55,82,.78), rgba(7,29,45,.82) 58%, rgba(7,45,40,.62));
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,.035),
                0 9px 28px rgba(0,0,0,.11);
        }
        .til-sync-icon {
            display: flex;
            align-items: center;
            justify-content: center;
            flex: 0 0 25px;
            width: 25px;
            height: 25px;
            border: 1px solid #42cfff;
            border-radius: 50%;
            color: #61ddff;
            font-weight: 900;
            font-size: .78rem;
            box-shadow: 0 0 12px rgba(66,207,255,.12);
        }
        .til-sync-copy {
            min-width: 0;
            flex: 1 1 auto;
        }
        .til-sync-title {
            overflow: hidden;
            color: #dcebf4;
            font-size: .82rem;
            line-height: 1.2;
            white-space: nowrap;
            text-overflow: ellipsis;
        }
        .til-sync-sub {
            margin-top: 4px;
            color: #7fa4bb;
            font-size: .68rem;
        }
        .til-sync-badge {
            flex: 0 0 auto;
            padding: 5px 8px;
            border: 1px solid rgba(66,231,139,.44);
            border-radius: 5px;
            color: #56e899;
            background: rgba(24,99,63,.22);
            font-size: .59rem;
            font-weight: 900;
            letter-spacing: .08em;
        }
        .til-sync-badge span {
            display: inline-block;
            width: 5px;
            height: 5px;
            margin-right: 4px;
            border-radius: 999px;
            background: #4feb91;
            box-shadow: 0 0 8px rgba(79,235,145,.75);
        }
        .til-github-mark {
            display: flex;
            align-items: center;
            justify-content: center;
            flex: 0 0 28px;
            width: 28px;
            height: 28px;
            color: #58ec9b;
            border: 1px solid rgba(76,230,148,.35);
            border-radius: 6px;
            background: rgba(16,78,51,.22);
        }

        /* Upload workspace. */
        .til-panel-heading {
            display: flex;
            align-items: end;
            justify-content: space-between;
            gap: 16px;
            margin: 0 0 8px;
            padding: 1px 2px 4px;
        }
        .til-panel-title {
            color: #f5f9fd;
            font-size: 1.04rem;
            font-weight: 790;
            letter-spacing: -.02em;
        }
        .til-panel-sub {
            margin-top: 4px;
            color: #7894aa;
            font-size: .72rem;
            line-height: 1.38;
        }
        .til-panel-chip {
            flex: 0 0 auto;
            padding: 5px 7px;
            border: 1px solid rgba(91,211,255,.24);
            border-radius: 4px;
            color: #69cfff;
            background: rgba(19,77,109,.18);
            font-size: .55rem;
            font-weight: 900;
            letter-spacing: .10em;
        }
        [data-testid="stFileUploader"] {
            overflow: hidden !important;
            border: 1px solid rgba(61,226,161,.32) !important;
            border-radius: 8px !important;
            background:
                linear-gradient(135deg, rgba(7,35,49,.77), rgba(5,23,38,.68)) !important;
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,.025),
                0 0 24px rgba(45,220,163,.035) !important;
        }
        [data-testid="stFileUploaderDropzone"] {
            min-height: 112px !important;
            border: 1px dashed rgba(65,231,167,.58) !important;
            border-radius: 7px !important;
            background:
                radial-gradient(circle at 12% 50%, rgba(52,224,165,.10), transparent 24%),
                linear-gradient(110deg, rgba(8,42,52,.70), rgba(7,26,42,.70)) !important;
        }
        [data-testid="stFileUploaderDropzone"] button {
            border-color: rgba(64,230,163,.48) !important;
            color: #dffbf0 !important;
            background:
                linear-gradient(100deg, rgba(15,110,77,.65), rgba(12,80,76,.58)) !important;
            box-shadow: 0 0 17px rgba(53,226,158,.09) !important;
        }

        /* Right evidence rail. */
        .til-rail-card {
            margin: 0 0 13px;
            padding: 14px 14px;
            overflow: hidden;
            border: 1px solid rgba(84,190,238,.19);
            border-radius: 8px;
            background:
                linear-gradient(145deg, rgba(7,28,45,.92), rgba(5,18,30,.86));
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,.022),
                0 10px 28px rgba(0,0,0,.12);
        }
        .til-rail-title {
            color: #ecf5fb;
            font-size: .84rem;
            font-weight: 780;
            letter-spacing: -.015em;
            margin-bottom: 12px;
        }
        .til-gauge {
            --coverage: 0deg;
            position: relative;
            width: 116px;
            height: 116px;
            margin: 4px auto 10px;
            border-radius: 50%;
            background:
                conic-gradient(from 0deg, #45dd9b 0deg, #48cdf7 var(--coverage), rgba(37,73,91,.38) var(--coverage), rgba(37,73,91,.38) 360deg);
            box-shadow:
                0 0 0 5px rgba(39,106,128,.16),
                0 0 26px rgba(61,220,186,.12);
        }
        .til-gauge::before {
            content: "";
            position: absolute;
            inset: 8px;
            border-radius: 50%;
            background: #061522;
            border: 1px solid rgba(65,216,235,.24);
        }
        .til-gauge::after {
            content: "";
            position: absolute;
            inset: -7px;
            border: 1px solid rgba(65,216,235,.18);
            border-radius: 50%;
        }
        .til-gauge-inner {
            position: absolute;
            z-index: 2;
            inset: 0;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-direction: column;
        }
        .til-gauge-value {
            color: #f7fbff;
            font-size: 1.42rem;
            line-height: 1;
            font-weight: 880;
        }
        .til-gauge-label {
            margin-top: 5px;
            color: #7e9eb2;
            font-size: .48rem;
            line-height: 1.18;
            font-weight: 850;
            letter-spacing: .10em;
            text-align: center;
        }
        .til-coverage-state {
            color: #52e597;
            font-size: .74rem;
            font-weight: 790;
        }
        .til-coverage-copy {
            margin-top: 4px;
            color: #829bad;
            font-size: .66rem;
            line-height: 1.38;
        }
        .til-rail-spark {
            width: 100%;
            height: 30px;
            margin-top: 10px;
        }
        .til-quality-row {
            display: flex;
            gap: 9px;
            align-items: flex-start;
            margin: 0 0 11px;
        }
        .til-quality-row:last-child {
            margin-bottom: 0;
        }
        .til-quality-icon {
            flex: 0 0 17px;
            margin-top: 1px;
            font-size: .78rem;
        }
        .til-quality-row strong {
            display: block;
            font-size: .69rem;
            line-height: 1.1;
        }
        .til-quality-row small {
            display: block;
            margin-top: 3px;
            color: #7892a6 !important;
            font-size: .60rem;
            line-height: 1.35;
        }
        .til-quality-row.high .til-quality-icon,
        .til-quality-row.high strong { color: #4be895; }
        .til-quality-row.medium .til-quality-icon,
        .til-quality-row.medium strong { color: #f1bd55; }
        .til-quality-row.low .til-quality-icon,
        .til-quality-row.low strong { color: #ec6878; }

        .til-pipeline-row {
            display: grid;
            grid-template-columns: 28px 1fr;
            column-gap: 7px;
            align-items: center;
            padding: 7px 0;
            border-bottom: 1px solid rgba(85,180,224,.09);
        }
        .til-pipeline-row:last-child {
            border-bottom: 0;
        }
        .til-pipeline-row span {
            grid-row: 1 / span 2;
            color: #55cff5;
            font-size: .63rem;
            font-weight: 900;
            letter-spacing: .06em;
        }
        .til-pipeline-row b {
            color: #dbe8f1;
            font-size: .68rem;
            line-height: 1.1;
        }
        .til-pipeline-row em {
            color: #718da1;
            font-size: .58rem;
            line-height: 1.1;
            font-style: normal;
        }

        /* Recent source cards. */
        .til-section-row {
            display: flex;
            align-items: end;
            gap: 16px;
            margin: 1.55rem 0 .72rem;
        }
        .til-section-kicker {
            color: #4fcff4;
            font-size: .56rem;
            font-weight: 900;
            letter-spacing: .15em;
        }
        .til-section-title {
            margin-top: 4px;
            color: #edf5fa;
            font-size: .96rem;
            font-weight: 760;
        }
        .til-section-line {
            flex: 1 1 auto;
            height: 1px;
            margin-bottom: 5px;
            background:
                linear-gradient(90deg, rgba(75,201,247,.22), rgba(68,226,143,.12), transparent);
        }
        .til-recent-source-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 12px;
            margin-bottom: 16px;
        }
        .til-source-card {
            position: relative;
            display: flex;
            min-width: 0;
            gap: 10px;
            align-items: center;
            padding: 11px 12px;
            overflow: hidden;
            border: 1px solid rgba(84,185,234,.17);
            border-radius: 7px;
            background:
                linear-gradient(135deg, rgba(7,27,44,.90), rgba(6,19,31,.86));
        }
        .til-source-card::before {
            content: "";
            position: absolute;
            left: 0;
            top: 0;
            bottom: 0;
            width: 2px;
            background:
                linear-gradient(180deg, #4ad0ff, #45e791);
        }
        .til-source-fileicon {
            display: flex;
            align-items: center;
            justify-content: center;
            flex: 0 0 34px;
            width: 34px;
            height: 38px;
            border: 1px solid rgba(75,202,252,.30);
            border-radius: 5px;
            color: #5dd8ff;
            background: rgba(23,90,128,.15);
            font-size: .92rem;
        }
        .til-source-main {
            min-width: 0;
            flex: 1 1 auto;
        }
        .til-source-title {
            overflow: hidden;
            color: #eef6fb;
            font-size: .73rem;
            font-weight: 760;
            white-space: nowrap;
            text-overflow: ellipsis;
        }
        .til-source-author,
        .til-source-meta {
            overflow: hidden;
            margin-top: 2px;
            color: #758da1;
            font-size: .59rem;
            white-space: nowrap;
            text-overflow: ellipsis;
        }
        .til-source-status {
            align-self: flex-start;
            flex: 0 0 auto;
            font-size: .50rem;
            font-weight: 900;
            letter-spacing: .07em;
        }
        .til-source-status.ready {
            color: #4ee695;
        }
        .til-source-status.working {
            color: #f0be5e;
        }

        @media (max-width: 1150px) {
            .til-kpi-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
            .til-recent-source-grid {
                grid-template-columns: 1fr;
            }
            .til-market-mesh {
                width: 54%;
                opacity: .58;
            }
        }

        /* ---------- Stock Strategy Finder ---------- */
        .til-finder-intro {
            display: grid;
            grid-template-columns: 48px 1fr auto;
            gap: 14px;
            align-items: center;
            margin: 0 0 16px;
            padding: 15px 16px;
            border: 1px solid rgba(74,205,255,.20);
            border-radius: 8px;
            background:
                radial-gradient(circle at 90% 10%, rgba(65,229,151,.08), transparent 28%),
                linear-gradient(110deg, rgba(7,31,50,.90), rgba(5,19,32,.84));
            box-shadow: inset 0 1px 0 rgba(255,255,255,.025), 0 10px 28px rgba(0,0,0,.11);
        }
        .til-finder-intro-icon {
            display:flex;align-items:center;justify-content:center;
            width:42px;height:42px;
            transform: rotate(45deg);
            border:1px solid rgba(76,224,174,.48);
            color:#61e9b2;
            background:rgba(22,98,92,.22);
            box-shadow:0 0 20px rgba(69,222,179,.10);
            font-size:.85rem;
        }
        .til-finder-intro-icon::first-letter { transform: rotate(-45deg); }
        .til-finder-intro-title {
            color:#f5fbff;font-size:1.02rem;font-weight:820;letter-spacing:-.02em;
        }
        .til-finder-intro-copy {
            margin-top:4px;color:#88a5ba;font-size:.74rem;line-height:1.48;max-width:980px;
        }
        .til-finder-policy {
            padding:8px 11px;border:1px solid rgba(71,230,148,.38);border-radius:6px;
            background:rgba(10,72,47,.25);text-align:center;min-width:90px;
        }
        .til-finder-policy span {
            display:block;color:#719687;font-size:.49rem;font-weight:900;letter-spacing:.12em;
        }
        .til-finder-policy strong {
            display:block;margin-top:3px;color:#54eba0;font-size:.78rem;letter-spacing:.08em;
            text-shadow:0 0 10px rgba(84,235,160,.18);
        }
        .til-finder-stats {
            display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:12px 0 10px;
        }
        .til-finder-stats > div {
            position:relative;overflow:hidden;padding:12px 13px;border:1px solid rgba(73,185,236,.17);
            border-radius:7px;background:linear-gradient(145deg,rgba(8,27,44,.82),rgba(5,18,30,.72));
        }
        .til-finder-stats > div::before {
            content:"";position:absolute;left:0;top:0;width:42%;height:1px;
            background:linear-gradient(90deg,#4fd5ff,#49e894,transparent);
        }
        .til-finder-stats span {
            display:block;color:#708da3;font-size:.51rem;font-weight:900;letter-spacing:.11em;
        }
        .til-finder-stats strong {
            display:block;margin-top:6px;color:#f3f9fd;font-size:1.24rem;font-weight:850;letter-spacing:-.035em;
        }
        .til-finder-stats em {
            display:block;margin-top:4px;color:#5d9fba;font-size:.57rem;font-style:normal;
        }
        .til-finder-verdict {
            display:flex;align-items:center;justify-content:space-between;gap:20px;
            margin:18px 0 14px;padding:16px 18px;border-radius:8px;
            border:1px solid rgba(72,192,237,.20);
            background:linear-gradient(110deg,rgba(7,30,48,.90),rgba(5,20,32,.84));
        }
        .til-finder-verdict.ready {
            border-color:rgba(67,231,143,.42);
            background:linear-gradient(110deg,rgba(7,55,43,.86),rgba(5,24,34,.86));
        }
        .til-finder-verdict.promising {
            border-color:rgba(232,190,87,.34);
            background:linear-gradient(110deg,rgba(65,48,12,.46),rgba(6,24,36,.86));
        }
        .til-finder-verdict.reject {
            border-color:rgba(233,100,119,.34);
            background:linear-gradient(110deg,rgba(67,20,30,.46),rgba(6,22,34,.86));
        }
        .til-finder-verdict span {
            display:block;color:#7290a6;font-size:.52rem;font-weight:900;letter-spacing:.13em;
        }
        .til-finder-verdict strong {
            display:block;margin-top:5px;color:#f6fbff;font-size:1.15rem;font-weight:850;
        }
        .til-finder-verdict p {
            margin:5px 0 0;color:#91a9bb;font-size:.70rem;line-height:1.42;
        }
        .til-finder-score {
            flex:0 0 auto;color:#5ee6a2;font-size:2.2rem;font-weight:900;letter-spacing:-.06em;
        }
        .til-finder-score small {
            margin-left:3px;color:#7391a4;font-size:.68rem;font-weight:700;letter-spacing:0;
        }
        @media (max-width: 1000px) {
            .til-finder-stats {grid-template-columns:repeat(2,minmax(0,1fr));}
            .til-finder-intro {grid-template-columns:40px 1fr;}
            .til-finder-policy {grid-column:1 / -1;}
        }

        /* ---------- Polished spacing ---------- */
        [data-testid="stVerticalBlock"] > [data-testid="stElementContainer"] {
            margin-bottom: .05rem;
        }
        [data-testid="stForm"] {
            border-color: rgba(112,151,190,.11) !important;
            border-radius: 12px !important;
            background: rgba(9,22,36,.28) !important;
        }

        @media (max-width: 900px) {
            .block-container {
                padding-left: .85rem !important;
                padding-right: .85rem !important;
            }
            .til-hero {
                padding: 9px 12px !important;
            }
            .til-kicker {
                display: none !important;
            }
            .til-title {
                font-size: .98rem !important;
            }
            .til-pagehead {
                padding: 17px 18px !important;
            }
            .til-page-title {
                font-size: 1.65rem !important;
            }
            .til-page-step {
                font-size: 3.3rem !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
