# Cloudflare hardening for aireadsthenews.co

Short guide to close three security gaps for `aireadsthenews.co` (apex) and `www.aireadsthenews.co`. Cloudflare is the correct place to set all of these because it terminates TLS at the edge before requests reach GitHub Pages, and because GitHub Pages ignores `_headers` files. What this fixes: the Cloudflare Security Insights items titled "Domains without Always Use HTTPS" and "Domains without HSTS", plus the missing security response headers.

Do the sections in order. A is the highest priority and the prerequisite for B. C is nice to have.

Before you start: sign in to the Cloudflare dashboard at https://dash.cloudflare.com, then in the account home click the domain **aireadsthenews.co** to enter its zone. Every step below happens inside that zone. The left sidebar is where the section names live.

---

## A. Turn on "Always Use HTTPS"

Right now `http://aireadsthenews.co/` returns 200 instead of redirecting to https. This flips it to a 301 redirect for every plain-HTTP request, so no browser ever talks to the site over unencrypted HTTP.

1. In the left sidebar, click **SSL/TLS**. The section expands.
2. Click **Edge Certificates** underneath it.
3. Scroll down the Edge Certificates page until you see the row labeled **Always Use HTTPS**.
4. Click the toggle on the right side of that row so it turns blue (On).

That is it. There is no Save button on this page; the toggle takes effect within a minute or two. Prerequisite check: near the top of the SSL/TLS section, your **SSL/TLS encryption mode** must not be set to Off. If it is Off, the Always Use HTTPS toggle will not appear. It should already be Full or Full (strict) for a GitHub Pages origin, which is fine.

---

## B. Turn on HSTS (HTTP Strict Transport Security)

Right now the site sends no `strict-transport-security` header. Enabling HSTS tells browsers "for the next N months, refuse to talk to this domain over http, even if the user types http:// or clicks an old link." This is a defense against downgrade and cookie-injection attacks. It is a real commitment: while HSTS is active, if https ever breaks (expired cert, bad config) visitors get a hard error page and cannot click through. That is why you enable "Always Use HTTPS" in section A first, and why you start with a short duration.

Same page as section A: **SSL/TLS** → **Edge Certificates**.

1. On the Edge Certificates page, find the section labeled **HTTP Strict Transport Security (HSTS)**.
2. Click **Enable HSTS**.
3. Cloudflare pops up a warning dialog explaining that HSTS is a browser-level commitment. Read it, then click **I understand**.
4. Click **Next**.
5. You now see the HSTS configuration form. Set the fields exactly as follows:
   - **Enable HSTS**: On (should already be on from clicking Enable HSTS).
   - **Max Age Header**: choose **6 months** from the dropdown. This is the value `15552000` seconds in the raw HTTP header. You can raise this to 12 months later once you are confident nothing on the site depends on plain http.
   - **Apply HSTS policy to subdomains (includeSubDomains)**: On. The only subdomain in use is `www`, which is already served over https via Cloudflare, so this is safe.
   - **Preload**: Off. Do not turn this on. Preload submits your domain to a hardcoded list baked into Chrome, Firefox, Safari, and Edge. Removal takes months or years and is not guaranteed. Only turn preload on once you are certain you will keep the domain on https forever and have run at 12 month max-age with no issues.
   - **No-Sniff Header**: On is fine. It sets the same `X-Content-Type-Options: nosniff` header that section C also sets; either one covers it.
6. Click **Save**.

Verify a few minutes later by running the curl command at the bottom of this doc. You should see a `strict-transport-security: max-age=15552000; includeSubDomains` line in the response.

---

## C. Set custom security response headers via a Transform Rule (optional, lower priority)

This one is nice-to-have. It sets a Content Security Policy and a few other defense-in-depth response headers. GitHub Pages ignores the `static/_headers` file convention, so these must be applied at Cloudflare.

1. In the left sidebar, click **Rules**. The section expands.
2. Click **Overview** underneath it.
3. On the Rules Overview page, click the **Create rule** button, then choose **Response Header Transform Rule** from the menu.
4. In the **Rule name** field, enter: `Security response headers`.
5. Under **When incoming requests match**, click the option to apply the rule to **All incoming requests** (there is a radio or toggle for this, next to a "Custom filter expression" option). Leave the expression editor alone.
6. Scroll down to the header modification section. For the first header, in the action dropdown pick **Set static**. Enter these values, then click **Set new header** to add another row, and repeat for each of the five headers below. You can have up to 30 headers per rule, so five is fine.

   | Action | Header name | Value |
   |---|---|---|
   | Set static | `Content-Security-Policy` | `default-src 'self'; script-src 'self' https://static.cloudflareinsights.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self' https://cloudflareinsights.com; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; upgrade-insecure-requests` |
   | Set static | `X-Frame-Options` | `DENY` |
   | Set static | `X-Content-Type-Options` | `nosniff` |
   | Set static | `Referrer-Policy` | `strict-origin-when-cross-origin` |
   | Set static | `Permissions-Policy` | `geolocation=(), microphone=(), camera=()` |

7. Click **Deploy** at the bottom of the page. The rule goes live within a minute or so.

Notes on the headers:
- The Content Security Policy intentionally allowlists `static.cloudflareinsights.com` (for the script) and `cloudflareinsights.com` (for the beacon connection) so Cloudflare Web Analytics keeps working. Do not strip those entries or analytics stops reporting.
- The CSP also allowlists `fonts.googleapis.com` and `fonts.gstatic.com` for the site's web fonts. Adjust if the site stops loading fonts from a different host.
- Do NOT add `Strict-Transport-Security` here. HSTS has its own dedicated toggle in section B, and setting it in two places at once causes duplicate headers and confusion.
- If you later add a form or a third-party embed, you will need to widen the CSP `form-action`, `frame-ancestors`, or `connect-src` accordingly. The current values assume a fully static read-only site with no forms and no iframes.

---

## Verification

After all three sections are live, run these two commands from any terminal:

```
curl -sSI http://aireadsthenews.co/
curl -sSI https://aireadsthenews.co/
```

Expected:
- The first (http) should show `HTTP/1.1 301 Moved Permanently` and a `Location: https://aireadsthenews.co/` header. That confirms section A.
- The second (https) should show a `strict-transport-security: max-age=15552000; includeSubDomains` line. That confirms section B. If you also did section C, it should additionally show `content-security-policy:`, `x-frame-options: DENY`, `x-content-type-options: nosniff`, `referrer-policy: strict-origin-when-cross-origin`, and `permissions-policy: geolocation=(), microphone=(), camera=()`.
