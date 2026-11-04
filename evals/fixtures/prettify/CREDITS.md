# Credits — evals/fixtures/prettify

Every fixture is derived from a photograph in `evals/golden-provisional/photos/`, whose
Wikimedia Commons provenance and licence are recorded in
[`evals/golden-provisional/CREDITS.md`](../../golden-provisional/CREDITS.md). Nothing here
was downloaded separately from a logo library: the labels, wordmarks and folds are drawn by
`build_fixtures.py`.

The derived files (`flat/*.png`, `alpha/*.png`) are **modified** copies — composited onto the
flat-lay backdrop, rescaled to 480 px, and overlaid with synthetic print and folds — so the
"adapted material" clause of CC BY applies to them exactly as it does to the source, and the
attribution chain is this table plus the source table.

| fixture | source photo (golden-provisional id) | Commons file | licence | author |
|---|---|---|---|---|
| logo-01-tee-chest | top-02 | [File:T-shirt2.jpg](https://commons.wikimedia.org/wiki/File:T-shirt2.jpg) | Public domain | Elkagye |
| logo-02-breeches-waistband | bottom-03 | [File:Breeches (AM 1973.193-3).jpg](https://commons.wikimedia.org/wiki/File:Breeches_(AM_1973.193-3).jpg) | CC BY 4.0 | not researched |
| logo-03-sneaker-side | shoe-02 | [File:Goodyear sneakers, 1890s, Goodyear Rubber Manufacturing Company - Bata Shoe Museum - DSC00745-001.JPG](https://commons.wikimedia.org/wiki/File:Goodyear_sneakers,_1890s,_Goodyear_Rubber_Manufacturing_Company_-_Bata_Shoe_Museum_-_DSC00745-001.JPG) | CC0 | Daderot |
| logo-04-belt-stamp | acc-03 | [File:Belt (USA), late 19th century (CH 18188479).jpg](https://commons.wikimedia.org/wiki/File:Belt_(USA),_late_19th_century_(CH_18188479).jpg) | Public domain | Unknown artist |
| logo-05-jacket-emblem | outer-01 | [File:Canadienne civile ayant appartenu à Bertrand Branet, 2002.2151(3).jpg](https://commons.wikimedia.org/wiki/File:Canadienne_civile_ayant_appartenu_%C3%A0_Bertrand_Branet,_2002.2151(3).jpg) | CC0 | Anonymous |
| crease-01-breeches-leg | bottom-01 | [File:Breeches (AM 1973.193-1).jpg](https://commons.wikimedia.org/wiki/File:Breeches_(AM_1973.193-1).jpg) | CC BY 4.0 | not researched |
| crease-02-tee-body | top-01 | [File:Guards Fest 2024 (53479474703).jpg](https://commons.wikimedia.org/wiki/File:Guards_Fest_2024_(53479474703).jpg) | CC BY 2.0 | Erik Drost |
| crease-03-tennis-shoe | shoe-04 | [File:Shoes, leather tennis (pair) (AM 2017.30.1-9).jpg](https://commons.wikimedia.org/wiki/File:Shoes,_leather_tennis_(pair)_(AM_2017.30.1-9).jpg) | CC BY 4.0 | Adidas |
| crease-04-breeches-back | bottom-06 | [File:Breeches MET 1972.139.7 B.jpg](https://commons.wikimedia.org/wiki/File:Breeches_MET_1972.139.7_B.jpg) | CC0 | not stated |

The garment silhouettes come from the provisional set's algorithmic masks
(`algorithmic:background_colour_distance`), not from hand-drawn masks; the alpha PNGs in
`alpha/` are those masks, rescaled.

**Attribution requirement:** four of the nine sources are CC BY, so any redistribution of this
directory must keep this table. `manifest.json` repeats the licence and Commons page per case,
which is what the eval report quotes.
