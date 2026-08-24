"""Server-rendered HTML for connect landing / success / error pages."""

from __future__ import annotations

from html import escape


# Wrap page content in the shared HTML shell (styles, layout).
def _shell(*, title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{escape(title)}</title>
  <style>
    :root {{
      --bg: #0f1419;
      --fg: #e7ecf1;
      --muted: #9aa7b5;
      --accent: #3d9a6a;
      --danger: #c45c5c;
      --card: #1a222c;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: "Segoe UI", system-ui, sans-serif;
      background:
        radial-gradient(ellipse at 20% 0%, #1a3a2a 0%, transparent 50%),
        radial-gradient(ellipse at 80% 100%, #1a2838 0%, transparent 45%),
        var(--bg);
      color: var(--fg);
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 1.5rem;
    }}
    main {{
      width: min(28rem, 100%);
      background: var(--card);
      border: 1px solid #2a3542;
      border-radius: 12px;
      padding: 1.75rem 1.5rem;
    }}
    h1 {{
      font-size: 1.35rem;
      font-weight: 650;
      margin: 0 0 0.75rem;
      letter-spacing: -0.02em;
    }}
    p {{
      margin: 0 0 0.85rem;
      color: var(--muted);
      line-height: 1.45;
      font-size: 0.95rem;
    }}
    .phone {{
      color: var(--fg);
      font-weight: 600;
      font-variant-numeric: tabular-nums;
    }}
    ul {{
      margin: 0 0 1.25rem;
      padding-left: 1.1rem;
      color: var(--muted);
      font-size: 0.92rem;
      line-height: 1.5;
    }}
    a.btn, button.btn {{
      display: inline-block;
      width: 100%;
      text-align: center;
      text-decoration: none;
      border: none;
      border-radius: 8px;
      padding: 0.85rem 1rem;
      font-size: 1rem;
      font-weight: 600;
      cursor: pointer;
      background: var(--accent);
      color: #fff;
    }}
    a.btn:hover, button.btn:hover {{ filter: brightness(1.08); }}
    .brand {{
      font-size: 0.8rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
      margin-bottom: 0.6rem;
    }}
    .err {{ color: var(--danger); }}
    .ok {{ color: var(--accent); }}
  </style>
</head>
<body>
  <main>
    <div class="brand">ggbot</div>
    {body}
  </main>
</body>
</html>
"""


def landing_page(
    *,
    masked_phone: str,
    start_url: str,
    title: str,
    body_pt: str,
    cta: str,
) -> str:
    """Render a connect landing page.

    ``body_pt`` may include a single ``{phone}`` placeholder (registry copy);
    it is replaced with a styled, escaped masked phone.
    """
    before, sep, after = body_pt.partition("{phone}")
    if sep:
        body_html = (
            f"{escape(before)}"
            f'<span class="phone">{escape(masked_phone)}</span>'
            f"{escape(after)}"
        )
    else:
        body_html = escape(body_pt)

    body = f"""
    <h1>{escape(title)}</h1>
    <p>{body_html}</p>
    <p>Você pode revogar o acesso a qualquer momento nas configurações da conta.</p>
    <a class="btn" href="{escape(start_url)}">{escape(cta)}</a>
    """
    return _shell(title=f"{title} · ggbot", body=body)


# Page shown after the user successfully connects Gmail/Calendar/etc.
def success_page(*, display_name: str | None = None) -> str:
    if display_name:
        detail = (
            f"{escape(display_name)} vinculado. Pode voltar pro WhatsApp — "
            "a confirmação chega por lá em instantes."
        )
    else:
        detail = (
            "Conta vinculada. Pode voltar pro WhatsApp — a confirmação "
            "chega por lá em instantes."
        )
    body = f"""
    <h1 class="ok">Conectado ✓</h1>
    <p>{detail}</p>
    """
    return _shell(title="Conectado · ggbot", body=body)


# Page shown when the connect link is bad, expired, or something went wrong.
def error_page(*, message: str) -> str:
    body = f"""
    <h1 class="err">Não deu pra conectar</h1>
    <p>{escape(message)}</p>
    <p>Peça um link novo no WhatsApp e tenta de novo.</p>
    """
    return _shell(title="Erro · ggbot", body=body)


def mask_phone(phone: str) -> str:
    """Mask a phone for the landing page: +55••••7766.

    Only shows a country code when the number is long enough to certainly have
    one; on shorter inputs the first two digits are not a country code, and
    keeping both ends would leave almost nothing masked.
    """
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) <= 4:
        return "••••"
    if len(digits) < 11:
        return f"••••{digits[-2:]}"
    return f"+{digits[:2]}••••{digits[-4:]}"
