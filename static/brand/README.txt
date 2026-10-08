Your logo and welcome picture
=============================

Drop files here; no restart needed (refresh the page).

logo.svg / logo.png / logo.webp / logo.jpg
    Shown next to the app name on the welcome (sign-in) page, and used as the
    browser-tab icon. Square works best, at least 256 x 256 pixels; a PNG or
    SVG with a transparent background looks best on the blue panel.
    Until a logo is added, a dashed "Logo" placeholder is shown.

hero.jpg / hero.png / hero.webp
    Optional picture behind the left half of the welcome page (it is darkened
    so the text stays readable). Landscape or portrait, about 1600 x 1200,
    under 1 MB. Without one, the panel is a deep blue gradient.

These files are public (anyone can see the welcome page), unlike the
background photos in static/backgrounds/, which only signed-in people see.

The app name ("Reflections") can be changed with the APP_NAME setting
(in .env on your own computer, or in render.yaml / Render's Environment tab).
