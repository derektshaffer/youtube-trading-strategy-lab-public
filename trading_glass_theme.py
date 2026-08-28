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
