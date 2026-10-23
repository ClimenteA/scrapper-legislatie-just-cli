import random


def random_chrome_ua():
    chrome_version = f"{random.randint(110, 139)}.0.{random.randint(4000, 6000)}.{random.randint(50, 150)}"
    safari_version = f"{random.randint(530, 617)}.1.{random.randint(1, 30)}"
    return (
        f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/{safari_version} "
        f"(KHTML, like Gecko) Chrome/{chrome_version} Safari/{safari_version}"
    )


def random_firefox_ua():
    firefox_version = f"{random.randint(115, 122)}.0"
    return (
        f"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:{firefox_version}) "
        f"Gecko/20100101 Firefox/{firefox_version}"
    )


def random_safari_ua():
    safari_version = f"{random.randint(604, 617)}.1.{random.randint(1, 20)}"
    return (
        f"Mozilla/5.0 (Macintosh; Intel Mac OS X 13_{random.randint(1, 6)}) "
        f"AppleWebKit/{safari_version} (KHTML, like Gecko) "
        f"Version/{random.randint(15, 17)}.0 Safari/{safari_version}"
    )


def get_random_headers():
    # Pick a random UA generator
    ua_generators = [random_chrome_ua, random_firefox_ua, random_safari_ua]
    user_agent = random.choice(ua_generators)()

    # Rotate Accept headers
    accept_options = [
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "text/html,application/xml;q=0.9,*/*;q=0.8",
        "text/html,application/xhtml+xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    ]

    # Rotate Accept-Language
    accept_language_options = [
        "en-US,en;q=0.9",
        "en-GB,en;q=0.8",
        "ro-RO,ro;q=0.9,en;q=0.8",
        "fr-FR,fr;q=0.9,en;q=0.8",
    ]

    headers = {
        "User-Agent": user_agent,
        "Accept": random.choice(accept_options),
        "Accept-Language": random.choice(accept_language_options),
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    return headers
