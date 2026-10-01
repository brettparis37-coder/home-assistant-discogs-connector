load("render.star", "render")
load("http.star", "http")

DEFAULT_BACKGROUND = "#14243a"
ACCENT = "#55d6c2"
TEXT = "#f3f6fa"
DETAIL = "#9cb3cc"
FRAME_DELAY_MS = 25
COVER_KEYS = ["cover_1", "cover_2", "cover_3", "cover_4", "cover_5", "cover_6", "cover_7", "cover_8"]
# Relative dwell weights: quick flips at first, then an increasing pause on
# the selected cover. The weights are scaled to about six seconds for any
# number of covers (up to the eight the Home Assistant script passes).
SHUFFLE_FRAME_WEIGHTS = [1, 1, 1, 2, 3, 4, 6, 14]
SHUFFLE_TOTAL_FRAMES = 240


def clean(value, fallback):
    if value == None or value == "" or value == "unknown" or value == "unavailable":
        return fallback
    return value


def cover_image(artwork_url):
    if artwork_url != "":
        response = http.get(artwork_url, ttl_seconds = 3600)
        if response.status_code == 200:
            return render.Image(src = response.body(), width = 32, height = 32)
    return render.Box(width = 32, height = 32, color = "#26394e")


def shuffle_page(artwork_url, background):
    return render.Stack(
        children = [
            render.Box(width = 64, height = 32, color = background),
            render.Padding(pad = (0, 0, 0, 0), child = cover_image(artwork_url)),
            render.Padding(
                pad = (34, 5, 0, 0),
                child = render.Text(content = "VINYL", font = "CG-pixel-3x5-mono", color = ACCENT),
            ),
            render.Padding(
                pad = (34, 14, 0, 0),
                child = render.Text(content = "SHUFFLE", font = "CG-pixel-3x5-mono", color = TEXT),
            ),
            render.Padding(
                pad = (34, 24, 0, 0),
                child = render.Text(content = "PICKING", font = "CG-pixel-3x5-mono", color = DETAIL),
            ),
        ],
    )


def details_page(config):
    title = clean(config.get("title"), "Unknown album")
    artist = clean(config.get("artist"), "Unknown artist")
    year = clean(config.get("year"), "")
    artwork_url = clean(config.get("artwork_url"), "")
    background = clean(config.get("background"), DEFAULT_BACKGROUND)
    cover = cover_image(artwork_url)
    year_label = year if year != "" else "YOUR COLLECTION"
    return render.Stack(
        children = [
            render.Box(width = 64, height = 32, color = background),
            render.Padding(pad = (0, 0, 0, 0), child = cover),
            render.Padding(
                pad = (34, 1, 0, 0),
                child = render.Text(content = "YOUR RANDOM LP", font = "CG-pixel-3x5-mono", color = ACCENT),
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
    )


def main(config):
    mode = clean(config.get("mode"), "details")
    background = clean(config.get("background"), DEFAULT_BACKGROUND)
    if mode == "shuffle":
        artwork_urls = []
        for key in COVER_KEYS:
            artwork_url = clean(config.get(key), "")
            if artwork_url != "":
                artwork_urls.append(artwork_url)
        if len(artwork_urls) == 0:
            artwork_urls = [""]

        frames = []
        remaining_frames = SHUFFLE_TOTAL_FRAMES
        remaining_weight = 0
        for index in range(len(artwork_urls)):
            remaining_weight += SHUFFLE_FRAME_WEIGHTS[index]
        for index in range(len(artwork_urls)):
            page = shuffle_page(artwork_urls[index], background)
            if index == len(artwork_urls) - 1:
                frame_count = remaining_frames
            else:
                weight = SHUFFLE_FRAME_WEIGHTS[index]
                frame_count = max(1, int((remaining_frames * weight / remaining_weight) + 0.5))
            frames += [page] * frame_count
            remaining_frames -= frame_count
            remaining_weight -= SHUFFLE_FRAME_WEIGHTS[index]
        selected_page = render.Animation(children = frames)
    else:
        selected_page = render.Animation(children = [details_page(config)] * 400)

    return render.Root(
        delay = FRAME_DELAY_MS,
        max_age = 900,
        show_full_animation = True,
        child = selected_page,
    )

