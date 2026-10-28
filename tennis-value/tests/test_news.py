"""Actualités des joueurs : lecture du flux RSS, mots-clés, cache."""
from datetime import datetime, timedelta, timezone

from tennisvalue.ingest import news

RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>x</title>
<item><title>Sinner forfait à Paris, douleur à la hanche - L'Équipe</title><link>https://a</link>
  <pubDate>{d1}</pubDate><source url="https://lequipe.fr">L'Équipe</source></item>
<item><title>Sinner: "I feel confident, my best tennis is back" - ATP Tour</title><link>https://b</link>
  <pubDate>{d2}</pubDate><source url="https://atptour.com">ATP Tour</source></item>
<item><title>Vieux titre sur une blessure - Le Monde</title><link>https://c</link>
  <pubDate>{d3}</pubDate><source url="https://lemonde.fr">Le Monde</source></item>
</channel></rss>"""


def _rss(now):
    f = lambda h: (now - timedelta(hours=h)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    return RSS.format(d1=f(2), d2=f(30), d3=f(24 * 20))


def test_parse_and_tags():
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    items = news.parse_rss(_rss(now))
    assert [it["source"] for it in items] == ["L'Équipe", "ATP Tour", "Le Monde"]
    assert items[0]["title"] == "Sinner forfait à Paris, douleur à la hanche"          # sans le nom du journal
    assert items[0]["tags"] == ["blessure"] and items[1]["tags"] == ["confiance"]
    assert abs((items[0]["published"] - (now - timedelta(hours=2))).total_seconds()) < 2
    assert news.tags("Alcaraz cruises into the final") == []
    assert news.tags("Rune pulls out with a knee injury") == ["blessure"]
    assert news.tags("Fatigué, Fils perd ses moyens et sa confiance") == ["fatigue", "confiance"]
    s = news.summary(items, now)
    assert (s["n"], s["blessure"], s["confiance"]) == (2, 1, 1)                        # le titre de 20 jours est exclu
    assert s["blessure_titre"].startswith("Sinner forfait")


def test_cache_and_errors():
    calls = []

    def fake(name):
        calls.append(name)
        if name == "panne":
            raise OSError("pas d'internet")
        return [{"title": f"{name} blessé", "link": "", "source": "", "published": None, "tags": ["blessure"]}]

    c = news.NewsCache(fetch=fake)
    assert c.get("Sinner", refresh=False)["summary"] is None                            # rien sans relevé
    assert c.get("Sinner")["summary"]["blessure"] == 1 and c.get("Sinner")["summary"]["blessure"] == 1
    assert calls == ["Sinner"]                                                          # 2e appel : en cache
    r = c.get("panne")
    assert r["error"] == "pas d'internet" and r["items"] == []
