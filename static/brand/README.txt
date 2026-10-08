Logo and icons
==============

These files are MADE FROM the master logo by a script; don't edit them by hand.

  Master:  brand-source/logo-original.png   (square, 1024 px or larger)
  Script:  uv run --with pillow python tools/make_icons.py

To change the logo: replace brand-source/logo-original.png, run the script,
then push. It writes:

  logo.png                welcome page (next to the app name)
  favicon.ico, favicon-32.png   browser tab: the handwriting "R", white on blue
                          (simpler than the logo, so it reads at 16 px)
  apple-touch-icon.png    iPhone / iPad "Add to Home Screen"
  icon-192.png, icon-512.png    "Install app" on Android and computers
  icon-maskable-512.png   Android's circle / rounded-square crops

Optional: hero.jpg / hero.png / hero.webp here puts a darkened picture behind
the left half of the welcome page (about 1600 x 1200, under 1 MB).

Everything in this folder is public (the welcome page is visible to anyone),
unlike static/backgrounds/, which only signed-in people can see.
