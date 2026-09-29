load("render.star", "render")
load("http.star", "http")

BACKGROUND = "#14243a"
ACCENT = "#55d6c2"
TEXT = "#f3f6fa"
DETAIL = "#9cb3cc"


def clean(value, fallback):
    if value == None or value == "" or value == "unknown" or value == "unavailable":
        return fallback
    return value


def main(config):
    title = clean(config.get("title"), "Unknown album")
    artist = clean(config.get("artist"), "Unknown artist")
    year = clean(config.get("year"), "")
    artwork_url = clean(config.get("artwork_url"), "")
    background = clean(config.get("background"), BACKGROUND)

    if artwork_url != "":
        response = http.get(artwork_url, ttl_seconds = 3600)
        if response.status_code == 200:
            cover = render.Image(src = response.body(), width = 32, height = 32)
        else:
            cover = render.Box(width = 32, height = 32, color = "#26394e")
    else:
        cover = render.Box(width = 32, height = 32, color = "#26394e")

    year_label = year if year != "" else "YOUR COLLECTION"
    return render.Root(
        delay = 25,
        max_age = 900,
        show_full_animation = True,
        child = render.Stack(
            children = [
                render.Box(width = 64, height = 32, color = background),
                render.Padding(pad = (0, 0, 0, 0), child = cover),
                render.Padding(
                    pad = (34, 1, 0, 0),
                    child = render.Text(content = "RANDOM PICK", font = "CG-pixel-3x5-mono", color = ACCENT),
                ),
                render.Padding(
                    pad = (34, 8, 0, 0),
                    child = render.Marquee(
                        width = 28, align = "start",
                        child = render.Text(content = title, font = "CG-pixel-3x5-mono", color = TEXT),
                    ),
                ),
                render.Padding(
                    pad = (34, 17, 0, 0),
                    child = render.Marquee(
                        width = 28, align = "start",
                        child = render.Text(content = artist, font = "CG-pixel-3x5-mono", color = TEXT),
                    ),
                ),
                render.Padding(
                    pad = (34, 26, 0, 0),
                    child = render.Text(content = year_label, font = "CG-pixel-3x5-mono", color = DETAIL),
                ),
            ],
        ),
    )
