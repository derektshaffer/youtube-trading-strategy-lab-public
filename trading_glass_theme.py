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
            --til-bg-0: #050d18;
            --til-bg-1: #071524;
            --til-bg-2: #0a1b2d;
            --til-panel: rgba(10, 24, 40, .80);
            --til-panel-strong: rgba(12, 29, 48, .94);
            --til-inner: rgba(17, 38, 61, .58);
            --til-line: rgba(105, 151, 197, .22);
            --til-line-strong: rgba(105, 174, 226, .38);
            --til-text: #f4f8ff;
            --til-muted: #91a9c5;
            --til-green: #37ef79;
            --til-green-soft: rgba(55, 239, 121, .13);
            --til-green-line: rgba(55, 239, 121, .46);
            --til-blue: #63cfff;
            --til-purple: #b85cff;
            --til-red: #ff5368;
            --til-amber: #ffc95a;
            --til-radius: 14px;
            --til-shadow: 0 14px 40px rgba(0, 0, 0, .22);
        }

        html,
        body,
        [data-testid="stAppViewContainer"],
        .stApp {
            background:
                radial-gradient(circle at 80% -8%, rgba(38, 120, 180, .13), transparent 34%),
                radial-gradient(circle at 16% 10%, rgba(40, 235, 128, .055), transparent 30%),
                linear-gradient(180deg, var(--til-bg-0) 0%, var(--til-bg-1) 48%, #06101d 100%) !important;
            color: var(--til-text) !important;
        }

        [data-testid="stHeader"] {
            background: rgba(5, 13, 24, .70) !important;
            backdrop-filter: blur(16px) !important;
            -webkit-backdrop-filter: blur(16px) !important;
        }

        .block-container {
            max-width: 1640px !important;
            padding-top: 1.15rem !important;
            padding-left: 1.2rem !important;
            padding-right: 1.2rem !important;
            padding-bottom: 2.2rem !important;
        }

        /* ---------- Main hero ---------- */
        .til-hero {
            position: relative;
            overflow: hidden;
            padding: 22px 25px !important;
            margin-bottom: 18px !important;
            border: 1px solid var(--til-line) !important;
            border-radius: 16px !important;
            background:
                radial-gradient(circle at 92% 0%, rgba(55,239,121,.08), transparent 30%),
                linear-gradient(145deg, rgba(12,29,48,.91), rgba(7,19,33,.82)) !important;
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,.04),
                0 14px 38px rgba(0,0,0,.18) !important;
            backdrop-filter: blur(18px) !important;
            -webkit-backdrop-filter: blur(18px) !important;
        }
        .til-hero::after {
            content: "";
            position: absolute;
            left: 0;
            top: 18px;
            bottom: 18px;
            width: 3px;
            border-radius: 999px;
            background: var(--til-green);
            box-shadow: 0 0 18px rgba(55,239,121,.38);
        }
        .til-title {
            color: #f5f9ff !important;
            font-size: 31px !important;
            font-weight: 900 !important;
            letter-spacing: -.035em !important;
            text-shadow: 0 1px 18px rgba(100,190,255,.05);
        }
        .til-sub {
            color: var(--til-muted) !important;
            line-height: 1.58 !important;
            margin-top: 7px !important;
            max-width: 1080px !important;
        }

        /* ---------- Sidebar ---------- */
        [data-testid="stSidebar"] {
            background:
                radial-gradient(circle at 20% 0%, rgba(55,239,121,.045), transparent 30%),
                linear-gradient(180deg, rgba(6,17,30,.98), rgba(5,14,25,.98)) !important;
            border-right: 1px solid rgba(105,151,197,.18) !important;
        }
        [data-testid="stSidebar"] > div:first-child {
            background: transparent !important;
        }
        [data-testid="stSidebar"] .block-container {
            padding-top: 1.05rem !important;
        }
        [data-testid="stSidebar"] h3 {
            color: #f3f8ff !important;
            font-size: 1.28rem !important;
            font-weight: 900 !important;
            letter-spacing: -.025em !important;
            margin-bottom: .4rem !important;
        }

        .st-key-til_workspace_section div[role="radiogroup"] {
            gap: .18rem !important;
        }
        .st-key-til_workspace_section div[role="radiogroup"] > label {
            position: relative !important;
            display: flex !important;
            align-items: center !important;
            min-height: 2.35rem !important;
            padding: .30rem .58rem !important;
            margin: 0 !important;
            border: 1px solid transparent !important;
            border-radius: 9px !important;
            background: transparent !important;
            transition:
                background .14s ease,
                border-color .14s ease,
                box-shadow .14s ease !important;
        }
        .st-key-til_workspace_section div[role="radiogroup"] > label:hover {
            background: rgba(17,38,61,.48) !important;
            border-color: rgba(99,207,255,.18) !important;
        }
        .st-key-til_workspace_section div[role="radiogroup"] > label:has(input:checked) {
            background:
                linear-gradient(145deg, rgba(29,119,67,.34), rgba(12,58,35,.27)) !important;
            border-color: var(--til-green-line) !important;
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,.045),
                0 0 18px rgba(55,239,121,.08) !important;
        }
        .st-key-til_workspace_section div[role="radiogroup"] > label p {
            color: #b8c8da !important;
            font-size: .96rem !important;
            font-weight: 680 !important;
            letter-spacing: -.015em !important;
            line-height: 1.1 !important;
        }
        .st-key-til_workspace_section div[role="radiogroup"] > label:has(input:checked) p {
            color: #f7fbff !important;
        }

        /* Hide native radio circles; selected glass row is the state indicator. */
        .st-key-til_workspace_section input[type="radio"] {
            position: absolute !important;
            opacity: 0 !important;
            width: 1px !important;
            height: 1px !important;
            pointer-events: none !important;
        }
        .st-key-til_workspace_section div[role="radiogroup"] > label > div:first-child {
            position: absolute !important;
            opacity: 0 !important;
            width: 1px !important;
            height: 1px !important;
            overflow: hidden !important;
            pointer-events: none !important;
        }

        /* Preserve the workflow section labels, but match scanner typography. */
        .st-key-til_workspace_section div[role="radiogroup"] > label:nth-child(1)::before,
        .st-key-til_workspace_section div[role="radiogroup"] > label:nth-child(4)::before,
        .st-key-til_workspace_section div[role="radiogroup"] > label:nth-child(9)::before,
        .st-key-til_workspace_section div[role="radiogroup"] > label:nth-child(13)::before {
            color: #718aa8 !important;
            font-size: .64rem !important;
            font-weight: 900 !important;
            letter-spacing: .13em !important;
        }

        [data-testid="stSidebar"] hr {
            border-color: rgba(105,151,197,.13) !important;
        }
        [data-testid="stSidebar"] [data-testid="stCaptionContainer"] {
            color: #7088a4 !important;
        }

        /* ---------- Cards / surfaces ---------- */
        .til-card,
        [data-testid="stExpander"] details,
        div[data-testid="stDataFrame"],
        div[data-testid="stTable"],
        [data-testid="stFileUploader"],
        [data-testid="stStatusWidget"] {
            border-color: var(--til-line) !important;
            background:
                linear-gradient(145deg, rgba(12,29,48,.84), rgba(8,20,34,.72)) !important;
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,.03),
                0 10px 30px rgba(0,0,0,.13) !important;
            backdrop-filter: blur(14px) !important;
            -webkit-backdrop-filter: blur(14px) !important;
        }

        .til-card {
            min-height: 132px !important;
            padding: 16px 17px !important;
            margin-bottom: 10px !important;
            border: 1px solid var(--til-line) !important;
            border-radius: 13px !important;
            transition: border-color .14s ease, transform .14s ease !important;
        }
        .til-card:hover {
            border-color: rgba(99,207,255,.31) !important;
            transform: translateY(-1px);
        }
        .til-card strong {
            color: #eef6ff !important;
        }
        .muted {
            color: var(--til-muted) !important;
        }

        [data-testid="stExpander"] details {
            border: 1px solid var(--til-line) !important;
            border-radius: 11px !important;
            overflow: hidden !important;
        }
        [data-testid="stExpander"] summary {
            color: #eaf3fc !important;
            font-weight: 720 !important;
        }

        div[data-testid="stDataFrame"],
        div[data-testid="stTable"] {
            border: 1px solid var(--til-line) !important;
            border-radius: 11px !important;
            overflow: hidden !important;
        }

        /* ---------- Typography ---------- */
        h1, h2, h3, h4, h5, h6 {
            color: #f3f8ff !important;
        }
        p, li {
            color: #d8e3ef;
        }
        [data-testid="stCaptionContainer"],
        small {
            color: var(--til-muted) !important;
        }
        a {
            color: var(--til-blue) !important;
        }
        hr {
            border-color: rgba(105,151,197,.14) !important;
        }

        /* ---------- Metrics ---------- */
        [data-testid="stMetric"] {
            padding: .75rem .85rem !important;
            border: 1px solid rgba(105,151,197,.18) !important;
            border-radius: 11px !important;
            background:
                linear-gradient(145deg, rgba(17,38,61,.57), rgba(10,26,43,.55)) !important;
            box-shadow: inset 0 1px 0 rgba(255,255,255,.02) !important;
        }
        [data-testid="stMetricLabel"] {
            color: #879fba !important;
        }
        [data-testid="stMetricValue"] {
            color: #f4f8ff !important;
        }

        /* ---------- Tabs ---------- */
        [data-baseweb="tab-list"] {
            gap: 5px !important;
            padding: 4px !important;
            border: 1px solid var(--til-line) !important;
            border-radius: 11px !important;
            background: rgba(8,21,35,.66) !important;
        }
        [data-baseweb="tab"] {
            border-radius: 8px !important;
            color: #aebfd2 !important;
            font-weight: 720 !important;
        }
        [aria-selected="true"][data-baseweb="tab"] {
            color: #f7fbff !important;
            background: rgba(55,239,121,.11) !important;
            box-shadow: inset 0 0 0 1px rgba(55,239,121,.27) !important;
        }
        [data-baseweb="tab-highlight"] {
            background-color: var(--til-green) !important;
        }

        /* ---------- Inputs ---------- */
        div[data-baseweb="select"] > div,
        [data-testid="stTextInput"] input,
        [data-testid="stNumberInput"] input,
        [data-testid="stTextArea"] textarea {
            color: #eef6ff !important;
            border-color: rgba(105,151,197,.26) !important;
            background: rgba(10,25,42,.78) !important;
            box-shadow: inset 0 1px 0 rgba(255,255,255,.02) !important;
        }
        div[data-baseweb="select"] > div:hover,
        [data-testid="stTextInput"] input:hover,
        [data-testid="stTextArea"] textarea:hover {
            border-color: rgba(99,207,255,.38) !important;
        }

        [data-testid="stFileUploader"] {
            border: 1px solid var(--til-line) !important;
            border-radius: 11px !important;
            padding: .45rem !important;
        }
        [data-testid="stFileUploaderDropzone"] {
            background: rgba(10,25,42,.60) !important;
            border-color: rgba(105,151,197,.24) !important;
        }

        /* ---------- Buttons ---------- */
        div[data-testid="stButton"] button,
        div[data-testid="stDownloadButton"] button {
            min-height: 2.42rem !important;
            border-radius: 9px !important;
            font-weight: 760 !important;
            transition:
                border-color .14s ease,
                background .14s ease,
                box-shadow .14s ease,
                transform .14s ease !important;
        }

        div[data-testid="stButton"] button[kind="primary"],
        div[data-testid="stDownloadButton"] button[kind="primary"] {
            color: #f7fff9 !important;
            border: 1px solid rgba(55,239,121,.52) !important;
            background:
                linear-gradient(145deg, rgba(28,138,74,.94), rgba(13,82,45,.94)) !important;
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,.08),
                0 0 20px rgba(55,239,121,.09) !important;
        }
        div[data-testid="stButton"] button[kind="primary"]:hover:not(:disabled),
        div[data-testid="stDownloadButton"] button[kind="primary"]:hover:not(:disabled) {
            border-color: rgba(91,255,146,.78) !important;
            background:
                linear-gradient(145deg, rgba(35,162,88,.97), rgba(16,96,53,.97)) !important;
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,.10),
                0 0 25px rgba(55,239,121,.15) !important;
            transform: translateY(-1px);
        }

        div[data-testid="stButton"] button[kind="secondary"],
        div[data-testid="stDownloadButton"] button[kind="secondary"] {
            color: #eaf3fc !important;
            border: 1px solid rgba(105,151,197,.28) !important;
            background: rgba(12,29,48,.66) !important;
            box-shadow: inset 0 1px 0 rgba(255,255,255,.025) !important;
        }
        div[data-testid="stButton"] button[kind="secondary"]:hover:not(:disabled),
        div[data-testid="stDownloadButton"] button[kind="secondary"]:hover:not(:disabled) {
            border-color: rgba(99,207,255,.38) !important;
            background: rgba(16,42,67,.76) !important;
        }

        div[data-testid="stButton"] button:disabled,
        div[data-testid="stDownloadButton"] button:disabled {
            opacity: .66 !important;
            color: #8398b0 !important;
            background: rgba(13,27,43,.68) !important;
            border-color: rgba(105,151,197,.14) !important;
            box-shadow: none !important;
        }

        /* ---------- Alerts / status ---------- */
        div[data-testid="stAlert"] {
            border-radius: 10px !important;
            border: 1px solid rgba(105,151,197,.20) !important;
            background: rgba(12,29,48,.72) !important;
        }
        [data-testid="stStatusWidget"] {
            border: 1px solid var(--til-line) !important;
            border-radius: 11px !important;
            overflow: hidden !important;
        }

        /* ---------- Progress ---------- */
        [data-testid="stProgress"] > div > div {
            background-color: var(--til-green) !important;
            box-shadow: 0 0 12px rgba(55,239,121,.20) !important;
        }
        [data-testid="stProgress"] > div {
            background: rgba(17,38,61,.65) !important;
        }

        /* ---------- Checkboxes / toggles ---------- */
        [data-baseweb="checkbox"] [aria-checked="true"],
        [data-testid="stToggle"] [aria-checked="true"] {
            background-color: var(--til-green) !important;
            border-color: var(--til-green) !important;
        }

        /* ---------- Popovers / menus ---------- */
        [data-baseweb="popover"],
        [data-baseweb="menu"] {
            background: rgba(7,20,34,.98) !important;
            border-color: var(--til-line) !important;
            box-shadow: var(--til-shadow) !important;
        }

        /* Keep the sidebar compact on smaller screens. */
        @media (max-width: 900px) {
            .block-container {
                padding-left: .8rem !important;
                padding-right: .8rem !important;
            }
            .til-title {
                font-size: 27px !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
