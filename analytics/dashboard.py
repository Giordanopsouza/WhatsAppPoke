"""Streamlit dashboard over the agent's Postgres. Read-only, aggregates only.

    uv run streamlit run analytics/dashboard.py

Deployed as a third Railway service (see railway.analytics.json). Access is
gated by ANALYTICS_USER / ANALYTICS_PASSWORD.
"""

import hmac
import sys
from pathlib import Path

# `streamlit run` puts the *script's* directory on sys.path, not the project
# root, so `analytics` is not importable as a package without this.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import altair as alt  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from analytics import queries  # noqa: E402
from analytics.settings import AnalyticsSettings  # noqa: E402

settings = AnalyticsSettings()

st.set_page_config(page_title="wpp-agent · analytics", page_icon="📊", layout="wide")

WEEKDAYS = {1: "seg", 2: "ter", 3: "qua", 4: "qui", 5: "sex", 6: "sáb", 7: "dom"}


def check_password() -> bool:
    """User/password gate. Credentials never enter session_state."""
    if st.session_state.get("auth_ok"):
        return True

    st.title("📊 wpp-agent · analytics")
    with st.form("login"):
        user = st.text_input("Usuário")
        password = st.text_input("Senha", type="password")
        submitted = st.form_submit_button("Entrar")

    if submitted:
        # compare_digest on both fields: constant-time, no early exit on user.
        ok = hmac.compare_digest(user, settings.analytics_user) & hmac.compare_digest(
            password, settings.analytics_password
        )
        if ok:
            st.session_state["auth_ok"] = True
            st.rerun()
        st.error("Usuário ou senha inválidos.")
    return False


@st.cache_resource
def get_engine():
    # pool_pre_ping: Supabase's pooler drops idle connections; without it the
    # first query after an idle period fails instead of reconnecting.
    # prepare_threshold=None: the pooler runs in transaction mode, where a
    # server-side prepared statement can land on a different backend than the
    # one that later executes it.
    return create_engine(
        settings.sync_database_url,
        pool_pre_ping=True,
        pool_size=2,
        connect_args={"prepare_threshold": None},
    )


@st.cache_data(ttl=300)
def run(sql: str, days: int, excluded: tuple[str, ...]) -> pd.DataFrame:
    params = {"tz": settings.analytics_tz, "days": days, "excluded": list(excluded)}
    with get_engine().connect() as conn:
        return pd.read_sql_query(text(sql), conn, params=params)


def main() -> None:
    st.title("📊 wpp-agent · analytics")

    with st.sidebar:
        st.header("Filtros")
        days = st.slider("Período (dias)", 7, 180, 30, step=7)
        hide_test = st.toggle(
            "Ocultar números de teste",
            value=bool(settings.excluded_phones),
            disabled=not settings.excluded_phones,
            help="Configurado em ANALYTICS_EXCLUDE_PHONES.",
        )
        if st.button("Recarregar dados"):
            st.cache_data.clear()
            st.rerun()

    excluded = tuple(settings.excluded_phones) if hide_test else ()

    kpis = run(queries.KPIS, days, excluded).iloc[0]
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Mensagens recebidas", int(kpis["recebidas"]))
    c2.metric("Contatos ativos", int(kpis["contatos_ativos"]))
    c3.metric("Ativos 24h", int(kpis["ativos_24h"]))
    c4.metric("Ativos 7d", int(kpis["ativos_7d"]))
    c5.metric("Msgs por contato", float(kpis["msgs_por_contato"] or 0))

    st.subheader("Atividade por dia")
    daily = run(queries.DAILY_ACTIVITY, days, excluded)
    if daily.empty:
        st.info("Sem mensagens no período.")
    else:
        left, right = st.columns([2, 1])
        left.line_chart(daily.set_index("dia")[["recebidas", "enviadas"]])
        right.bar_chart(daily.set_index("dia")[["ativos", "novos"]])

    st.subheader("Resumo semanal")
    st.dataframe(
        run(queries.WEEKLY_ACTIVITY, days, excluded), width="stretch"
    )

    st.subheader("Por contato")
    st.caption("Telefones mascarados; nenhum conteúdo de mensagem é exibido.")
    st.dataframe(
        run(queries.CONTACT_SUMMARY, days, excluded),
        width="stretch",
        hide_index=True,
    )

    st.subheader("Quando falam com o agente")
    heat = run(queries.HOURLY_HEATMAP, days, excluded)
    if heat.empty:
        st.info("Sem dados no período.")
    else:
        heat["dia_semana"] = heat["dow"].map(WEEKDAYS)
        st.altair_chart(
            alt.Chart(heat)
            .mark_rect()
            .encode(
                x=alt.X("hora:O", title="Hora do dia"),
                y=alt.Y(
                    "dia_semana:O",
                    title=None,
                    sort=[WEEKDAYS[i] for i in range(1, 8)],
                ),
                color=alt.Color("msgs:Q", title="Msgs", scale=alt.Scale(scheme="blues")),
                tooltip=["dia_semana", "hora", "msgs"],
            )
            .properties(height=220),
            width="stretch",
        )

    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("Funil de integrações")
        st.dataframe(
            run(queries.INTEGRATION_FUNNEL, days, excluded),
            width="stretch",
            hide_index=True,
        )
    with col_b:
        st.subheader("Uso de features")
        st.dataframe(
            run(queries.FEATURE_USAGE, days, excluded),
            width="stretch",
            hide_index=True,
        )

    st.subheader("Saúde dos jobs")
    st.caption("Queda de uso às vezes é job falhando, não usuário sumindo.")
    jobs = run(queries.JOB_HEALTH, days, excluded)
    if jobs.empty:
        st.info("Sem jobs no período.")
    else:
        st.bar_chart(jobs.pivot(index="dia", columns="status", values="jobs").fillna(0))


if check_password():
    main()
