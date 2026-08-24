0005. Webhook on a Cloudflare-proxied subdomain with SSL mode Full
Status: Accepted
Date: 2026-08-07

Context
The api service needs a stable public URL for Z-API to POST webhooks to,
replacing the cloudflared tunnel used in development. The owner's domain
`gglabs.ventures` is registered with DNS hosted on Cloudflare, and the
apex already serves a live site (200, proxied, Hostinger behind it).

Two choices had to be made, and the intuitive answer is wrong on both.

**Apex vs subdomain.** Cloudflare supports CNAME flattening, so pointing
the apex at Railway is technically possible — but it would replace the
site currently live there.

**Cloudflare proxy and SSL mode.** The instinct is to disable the proxy
(grey cloud) and, if proxying, to pick the strictest TLS setting
available. Railway's documentation says the opposite on both counts:
the record should be proxied for regular (non-wildcard) domains, and the
SSL/TLS mode must be **Full**, not Full (Strict). Strict mode requires
the origin certificate to match the hostname exactly, and Railway may
serve its default `*.up.railway.app` certificate during provisioning and
during each 90-day renewal — strict rejects that as a mismatch and
returns `526 Invalid SSL Certificate`. Flexible mode is worse: Cloudflare
would send plain HTTP, Railway redirects to HTTPS, and the result is an
`ERR_TOO_MANY_REDIRECTS` loop.

Decision
Serve the webhook from `api.gglabs.ventures`, proxied through Cloudflare,
with SSL/TLS mode **Full**.

1. Custom domain `api.gglabs.ventures` on the `api` service; apex and
   `www` stay free for a future marketing site (already assumed by task
   006's grooming note).
2. Cloudflare DNS: `CNAME api -> <target>.up.railway.app` **proxied
   (orange cloud)**, plus the `TXT _railway-verify.api` record Railway
   issues. Both are required — without the TXT record Railway returns
   404 rather than routing to the service.
3. Cloudflare SSL/TLS mode set to **Full**. Never Full (Strict), never
   Flexible.

Consequences
Positive:
- The apex site is untouched; the webhook gets its own hostname.
- Cloudflare's edge absorbs scanner traffic and DDoS in front of the
  webhook. Bot sweeps for `/.env`, `/.git/HEAD`, `/.ssh/id_rsa` began
  within minutes of the domain going live.
- Visitor-facing TLS is Cloudflare's, so the endpoint served traffic
  correctly while Railway's origin certificate was still issuing.
- The hostname is stable enough to use for the Google consent screen
  later (task 008).

Negative / tradeoffs:
- Railway's dashboard permanently shows the CNAME as
  `REQUIRES_UPDATE`, because it resolves the record and sees Cloudflare
  IPs rather than the `.up.railway.app` target. This is cosmetic and
  expected under the orange cloud — do not "fix" it.
- Full mode does not verify the origin certificate's hostname. Traffic
  is still encrypted end to end, and DNS points straight at Railway, so
  there is no realistic MITM position — but it is weaker than Strict.
- Certificate issuance can stall behind the proxy. Railway's documented
  workaround is to flip the record to grey cloud, wait for the cert to
  go green, then flip back to orange.
- Changing the hostname later means re-pointing the Z-API webhook by
  hand; that URL is not derived from anything in the repo.

Rejected alternatives:
- Apex `gglabs.ventures` — takes down the existing site.
- Grey cloud (DNS-only) — works, and is the right temporary state if a
  cert stalls, but gives up Cloudflare's edge protection on a publicly
  exposed webhook.
- Full (Strict) — breaks with 526 during certificate renewal windows.

Operational note: do not delete and re-add a custom domain to retry a
stuck certificate. Let's Encrypt rate-limits at 5 duplicate certificates
per domain per week, and tripping it locks issuance for 7 days even
after the underlying problem is fixed.

See also: `docs/deploy.md`, `docs/adr/0004-railway-builder-and-runtime-constraints.md`.
