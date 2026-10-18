# Tone Central

Tone Central lives in the **Library** tab: the **Tone Central** button in its
toolbar (after a notice that you are leaving for Roland's site and must follow its
[terms of service](https://bosstoneexchange.com/terms_and_conditions/)) opens the BOSS Tone Exchange in a dedicated, confined WebKit window (not
your system browser): no tabs or address bar, and navigation is restricted to
Roland/BOSS domains.  It needs WebKitGTK 4.1 and PyGObject from your
distribution (`webkit2gtk-4.1-0 python3-gi gir1.2-webkit2-4.1` on Debian/Ubuntu,
`webkit2gtk4.1 python3-gobject` on Fedora), and KataTone installed with
`--system-site-packages` so it can see them.

When KataTone is used from a browser (`katatone-web`), the button opens the
site in a new tab of that browser instead, with your own BOSS ID and no window on
the machine running KataTone.  Download the `.tsl` there and add it with
**Import .tsl…**; the rest of this page describes the desktop window.

Browsing and search are anonymous; downloading a liveset needs a BOSS ID.
Sign in on the site inside the window.  The session is kept in
`~/.local/share/katatone/boss-id-cookies.sqlite` (mode 0600; only the session
cookie is stored, not your password), and the status line under the toolbar
confirms it when you close the window.  KataTone never calls the Tone Exchange
API itself: everything happens in the site, in the window.  Clicking a liveset's
**Download** button saves the `.tsl` into the app's library directory
(`~/.local/share/katatone/livesets/`), and the liveset appears in the Library
tab's list straight away, selected.

Only download and share livesets you have the right to use.  BOSS, Katana,
BOSS TONE EXCHANGE and BOSS TONE CENTRAL are trademarks of Roland; this project
is unofficial.

## Terms of service

KataTone does not aim to violate the BOSS TONE EXCHANGE
[terms of service](https://bosstoneexchange.com/terms_and_conditions/), and it is
built to stay within them:

- It does not scrape, crawl or use bots, and it makes no requests of its own to the
  site. Tone Central is simply the official website shown in a window (or a tab of
  your own browser), and you browse and download by hand, as in any browser.
- You sign in with your own BOSS ID on the site itself. KataTone never sees your
  password; it only keeps the site's session cookie on your machine like a standard
  browser.
- It does not copy, mirror or resell the site or its content. Downloads go to your
  own library, and sharing livesets you do not have the right to share is up to you.
- The only change to the page is a small script that lets its Download button save
  a file in a single-window app to the software library location.

The site and everything on it belong to Roland and BOSS. This project is
independent and has no affiliation with them. If you are a rights holder and see a
problem, please open an issue.

