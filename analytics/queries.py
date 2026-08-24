"""Read-only SQL for the dashboard.

Every query takes the same three bind params:

- ``tz``      timezone used to cut days/weeks (a UTC cut moves the day boundary
              to 21h local, which silently splits every evening conversation)
- ``excluded`` list of phones to hide (test numbers); empty list = show all
- ``days``    lookback window

Message bodies are never selected — only counts and timestamps.
"""

# Contacts in scope, with a display label that never leaks a full phone number.
_SEL = """
WITH sel AS (
    SELECT
        c.id,
        coalesce(
            nullif(c.name, ''),
            '+' || left(c.phone, 2) || '••••' || right(c.phone, 4)
        ) AS label
    FROM contact c
    WHERE cardinality(:excluded ::text[]) = 0
       OR c.phone <> ALL (:excluded ::text[])
)
"""

DAILY_ACTIVITY = _SEL + """
, first_seen AS (
    SELECT m.contact_id, min((m.created_at AT TIME ZONE :tz)::date) AS d0
    FROM message m
    JOIN sel ON sel.id = m.contact_id
    WHERE m.direction = 'in'
    GROUP BY m.contact_id
)
, m AS (
    SELECT m.contact_id, m.direction, (m.created_at AT TIME ZONE :tz)::date AS d
    FROM message m
    JOIN sel ON sel.id = m.contact_id
    WHERE m.created_at >= now() - make_interval(days => :days)
)
SELECT
    m.d                                                        AS dia,
    count(*) FILTER (WHERE m.direction = 'in')                 AS recebidas,
    count(*) FILTER (WHERE m.direction = 'out')                AS enviadas,
    count(DISTINCT m.contact_id) FILTER (WHERE m.direction = 'in') AS ativos,
    (SELECT count(*) FROM first_seen f WHERE f.d0 = m.d)       AS novos
FROM m
GROUP BY m.d
ORDER BY m.d
"""

WEEKLY_ACTIVITY = _SEL + """
, m AS (
    SELECT
        m.contact_id,
        m.direction,
        date_trunc('week', (m.created_at AT TIME ZONE :tz))::date AS w
    FROM message m
    JOIN sel ON sel.id = m.contact_id
    WHERE m.created_at >= now() - make_interval(days => :days)
)
SELECT
    m.w                                                        AS semana,
    count(*) FILTER (WHERE m.direction = 'in')                 AS recebidas,
    count(*) FILTER (WHERE m.direction = 'out')                AS enviadas,
    count(DISTINCT m.contact_id) FILTER (WHERE m.direction = 'in') AS ativos,
    round(
        count(*) FILTER (WHERE m.direction = 'in')::numeric
        / nullif(count(DISTINCT m.contact_id)
                 FILTER (WHERE m.direction = 'in'), 0),
        1
    )                                                          AS msgs_por_ativo
FROM m
GROUP BY m.w
ORDER BY m.w
"""

CONTACT_SUMMARY = _SEL + """
SELECT
    sel.label                                                  AS contato,
    min((m.created_at AT TIME ZONE :tz)::date)                 AS primeira,
    max((m.created_at AT TIME ZONE :tz)::date)                 AS ultima,
    ((now() AT TIME ZONE :tz)::date
        - max((m.created_at AT TIME ZONE :tz)::date))          AS dias_inativo,
    count(*) FILTER (WHERE m.direction = 'in')                 AS msgs,
    count(DISTINCT (m.created_at AT TIME ZONE :tz)::date)
        FILTER (WHERE m.direction = 'in')                      AS dias_ativos,
    count(*) FILTER (WHERE coalesce(m.num_media, 0) > 0)       AS com_midia
FROM message m
JOIN sel ON sel.id = m.contact_id
WHERE m.created_at >= now() - make_interval(days => :days)
GROUP BY sel.id, sel.label
ORDER BY msgs DESC
"""

# When people actually talk to the agent: weekday x hour, inbound only.
HOURLY_HEATMAP = _SEL + """
SELECT
    to_char(m.created_at AT TIME ZONE :tz, 'ID')::int          AS dow,
    extract(hour FROM m.created_at AT TIME ZONE :tz)::int      AS hora,
    count(*)                                                   AS msgs
FROM message m
JOIN sel ON sel.id = m.contact_id
WHERE m.direction = 'in'
  AND m.created_at >= now() - make_interval(days => :days)
GROUP BY dow, hora
ORDER BY dow, hora
"""

# Connect link generated -> opened -> integration active, per provider.
INTEGRATION_FUNNEL = _SEL + """
SELECT
    coalesce(l.provider, i.provider)                           AS provider,
    coalesce(l.links, 0)                                       AS links_gerados,
    coalesce(l.usados, 0)                                      AS links_usados,
    coalesce(i.ativas, 0)                                      AS integracoes_ativas
FROM (
    SELECT cl.provider,
           count(*)                                        AS links,
           count(*) FILTER (WHERE cl.used_at IS NOT NULL)   AS usados
    FROM connect_link cl
    JOIN sel ON sel.id = cl.contact_id
    WHERE cl.created_at >= now() - make_interval(days => :days)
    GROUP BY cl.provider
) l
FULL OUTER JOIN (
    SELECT ig.provider, count(*) AS ativas
    FROM integration ig
    JOIN sel ON sel.id = ig.contact_id
    WHERE ig.status = 'active'
      AND ig.created_at >= now() - make_interval(days => :days)
    GROUP BY ig.provider
) i ON i.provider = l.provider
ORDER BY 1
"""

# Operational health: a drop in usage is sometimes just failing jobs.
JOB_HEALTH = _SEL + """
SELECT
    (j.created_at AT TIME ZONE :tz)::date                      AS dia,
    j.status                                                   AS status,
    count(*)                                                   AS jobs,
    round(avg(j.attempts), 2)                                  AS tentativas_media
FROM job j
JOIN sel ON sel.id = j.contact_id
WHERE j.created_at >= now() - make_interval(days => :days)
GROUP BY dia, j.status
ORDER BY dia, j.status
"""

# Feature adoption in the lookback window.
FEATURE_USAGE = _SEL + """
SELECT 'tarefas' AS feature,
       count(*)                                    AS total,
       count(DISTINCT t.contact_id)                AS contatos,
       count(*) FILTER (WHERE t.status = 'done')   AS concluidas
FROM task t JOIN sel ON sel.id = t.contact_id
WHERE t.created_at >= now() - make_interval(days => :days)
UNION ALL
SELECT 'lembretes',
       count(*),
       count(DISTINCT r.contact_id),
       count(*) FILTER (WHERE r.status = 'sent')
FROM reminder r JOIN sel ON sel.id = r.contact_id
WHERE r.created_at >= now() - make_interval(days => :days)
"""

KPIS = _SEL + """
, m AS (
    SELECT m.contact_id, m.direction, m.created_at
    FROM message m
    JOIN sel ON sel.id = m.contact_id
    WHERE m.created_at >= now() - make_interval(days => :days)
)
-- "Active" always means sent at least one inbound message: an outbound-only
-- contact was pinged by a reminder, not engaged.
SELECT
    count(*) FILTER (WHERE direction = 'in')                   AS recebidas,
    count(DISTINCT contact_id) FILTER (WHERE direction = 'in') AS contatos_ativos,
    (SELECT count(DISTINCT contact_id) FROM m
      WHERE direction = 'in' AND created_at >= now() - interval '1 day')  AS ativos_24h,
    (SELECT count(DISTINCT contact_id) FROM m
      WHERE direction = 'in' AND created_at >= now() - interval '7 days') AS ativos_7d,
    round(
        count(*) FILTER (WHERE direction = 'in')::numeric
        / nullif(count(DISTINCT contact_id) FILTER (WHERE direction = 'in'), 0), 1
    )                                                          AS msgs_por_contato
FROM m
"""
