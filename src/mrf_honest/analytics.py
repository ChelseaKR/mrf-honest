"""Google Analytics 4 on the published site, and only there (ADR 0009).

The owner decided on 2026-09-17 that every public site gets GA4, with its privacy pages and
claims updated to match. This module is the whole of it: the measurement ID, and the one inline
script that may load gtag.js. :func:`mrf_honest.site.render_site` puts that script in the head of
every page only when it is given an ID, and without one the render is byte-for-byte what it was,
with no script, no privacy page and no analytics sentence anywhere.

The script loads nothing -- no ``dataLayer``, no request to Google, no cookie -- unless all four of
these hold, and it checks them before it does anything else:

1. a measurement ID is set and well formed;
2. the page is served from ``https://chelseakr.github.io/mrf-honest/``. A local render, the
   Lighthouse job on 127.0.0.1 and a fork's Pages site all fail this, so none of them ever sends
   a hit to this property, and the resource budget that job asserts still sees no script;
3. the browser sends neither Global Privacy Control nor Do Not Track;
4. the reader has not opted out with the footer button, which sets :data:`OPT_OUT_KEY` in
   localStorage. The key names this project because every ``chelseakr.github.io`` project site
   shares one origin, and so one localStorage: a bare key would be read by the sibling sites too.

When it does load, it sets Consent Mode v2 defaults (the three ad signals denied everywhere;
``analytics_storage`` denied in the EEA, the UK and Switzerland, where GA sends cookieless pings,
and granted elsewhere), turns off Google signals and ad personalization, and sends
``page_location`` as the origin and path only.
"""

from __future__ import annotations

import json
import re
from typing import Final

__all__ = [
    "DENIED_REGIONS",
    "GA4_MEASUREMENT_ID",
    "OPT_OUT_KEY",
    "PUBLISHED_HOST",
    "PUBLISHED_PATH",
    "loader",
    "measurement_id_or_none",
]

#: The web stream of GA4 property 554843057 (14-month retention, Google signals off). Public: it
#: is in every page served. ``mrf-honest site`` passes it by default; ``--ga4-id ""`` renders a
#: site with no analytics.
GA4_MEASUREMENT_ID: Final[str] = "G-57DWCFVLWQ"

#: Where the published site is served. The script compares the page's own address with these at
#: runtime, rather than with the ``origin`` a render was given, so a fork deployed elsewhere with
#: the committed ID still sends nothing.
PUBLISHED_HOST: Final[str] = "chelseakr.github.io"
PUBLISHED_PATH: Final[str] = "/mrf-honest/"

#: The localStorage key the footer's opt-out writes. Named for this project; see the module
#: docstring for why a bare key would be wrong on a shared origin.
OPT_OUT_KEY: Final[str] = "mrf-honest:analytics-opt-out"

#: Where ``analytics_storage`` defaults to denied: the 27 EU member states, the rest of the EEA,
#: the UK and Switzerland. ISO 3166-1 alpha-2.
DENIED_REGIONS: Final[tuple[str, ...]] = (
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE",
    "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
    "IS", "LI", "NO",
    "GB", "CH",
)  # fmt: skip

#: What the footer control says. English only, like every other sentence the site renders.
MESSAGES: Final[dict[str, str]] = {
    "opt_out": "Opt out of analytics",
    "opt_in": "Opt back in",
    "signal": "Analytics is off: your browser sends Global Privacy Control or Do Not Track.",
    "off": "Analytics is off on this device.",
    "back_on": "Analytics is back on from the next page you open.",
    "not_saved": (
        "Your browser would not save this choice, so it lasts only until you leave this page."
    ),
}

_MEASUREMENT_ID: Final[re.Pattern[str]] = re.compile(r"G-[A-Z0-9]+")


def measurement_id_or_none(value: str | None) -> str | None:
    """A usable measurement ID, or ``None`` when none is configured.

    A malformed value raises rather than shipping a tag that silently records nothing: a
    Universal Analytics ``UA-`` ID, lowercase, or a stray space.
    """
    if value is None or value == "":
        return None
    if not _MEASUREMENT_ID.fullmatch(value):
        raise ValueError(f"GA4 measurement ID {value!r} is not of the form 'G-XXXXXXXXXX'")
    return value


def _js(value: object) -> str:
    """A value as a JavaScript literal that cannot close the ``<script>`` it sits in."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


#: Written compactly on purpose. The index page already sits close to the 60 KiB document cap in
#: perf/resource-budget.json, and these bytes are on every page; the readable account of what the
#: script does is the module docstring above and tests/test_analytics.py, which executes it.
#: Each guard keeps a line of its own so a negative control can remove exactly one.
_TEMPLATE: Final[str] = """<script id="analytics">
(function(){
var I=%(id)s,H=%(host)s,P=%(path)s,K=%(key)s,R=%(regions)s,M=%(messages)s,w=window,n=navigator,d=document,l=w.location;
if(!/^G-[A-Z0-9]+$/.test(I))return;
if(l.protocol!=="https:"||l.hostname!==H)return;
if(l.pathname!==%(bare_path)s&&l.pathname.indexOf(P)!==0)return;
var t=n.doNotTrack||w.doNotTrack||n.msDoNotTrack,s=n.globalPrivacyControl===true||t==="1"||t==="yes",o=false;
try{o=w.localStorage.getItem(K)==="1"}catch(e){}
d.addEventListener("DOMContentLoaded",function(){var c=d.getElementById("analytics-choice"),b=d.getElementById("analytics-opt-out"),u=d.getElementById("analytics-status");if(!c||!b||!u)return;c.hidden=false;if(s){u.textContent=M.signal;return}function f(m){b.textContent=o?M.opt_in:M.opt_out;u.textContent=m}b.hidden=false;f(o?M.off:"");b.addEventListener("click",function(){o=!o;w["ga-disable-"+I]=o;try{if(o)w.localStorage.setItem(K,"1");else w.localStorage.removeItem(K)}catch(e){f(M.not_saved);return}f(o?M.off:M.back_on)})});
if(n.globalPrivacyControl===true)return;
if(t==="1"||t==="yes")return;
if(o)return;
w.dataLayer=w.dataLayer||[];function g(){w.dataLayer.push(arguments)}
function a(v,r){var c={ad_storage:"denied",ad_user_data:"denied",ad_personalization:"denied",analytics_storage:v};if(r)c.region=r;return c}
g("consent","default",a("granted"));
g("consent","default",a("denied",R));
g("js",new Date());
g("config",I,{page_location:l.origin+l.pathname,allow_google_signals:false,allow_ad_personalization_signals:false});
var e=d.createElement("script");e.async=true;e.src="https://www.googletagmanager.com/gtag/js?id="+encodeURIComponent(I);d.head.appendChild(e)})();
</script>
"""  # noqa: E501 - page bytes, not Python: compact on purpose (see the comment above)


def loader(measurement_id: str) -> str:
    """The ``<script id="analytics">`` block for the head of every page.

    Raises ``ValueError`` on a malformed or empty ID, the same refusal
    :func:`measurement_id_or_none` makes, so no caller can render a tag nothing would accept.
    """
    checked = measurement_id_or_none(measurement_id)
    if checked is None:
        raise ValueError("an analytics loader needs a measurement ID")
    return _TEMPLATE % {
        "id": _js(checked),
        "host": _js(PUBLISHED_HOST),
        "path": _js(PUBLISHED_PATH),
        "bare_path": _js(PUBLISHED_PATH.rstrip("/")),
        "key": _js(OPT_OUT_KEY),
        "regions": _js(list(DENIED_REGIONS)),
        "messages": _js(MESSAGES),
    }
