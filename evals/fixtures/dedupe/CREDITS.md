# Credits — evals/fixtures/dedupe

Every pixel in this directory is derived from a licensed photograph in
`evals/golden-provisional/photos/`. **That directory's `CREDITS.md` is the authority**; this
file records the twelve sources actually used here, so the attribution chain does not have to
be reconstructed from a manifest that might be edited.

Two of the twelve are CC BY, which requires attribution wherever the image is redistributed —
that includes this directory. The `base/` files are re-encodes of the Commons thumbnails
(long edge 800 px, JPEG q82); the `exact/` files are byte-identical copies of a `base/` file;
the `variant/` files are derived from a `base/` file by the transform named in `manifest.json`.
No file here is a Commons original.

| id here | Commons file | licence | author | source file in this repo |
|---|---|---|---|---|
| bottom-01 | [File:Breeches (AM 1973.193-1).jpg](https://commons.wikimedia.org/wiki/File:Breeches_(AM_1973.193-1).jpg) | CC BY 4.0 | not researched (Auckland Museum) | `base/bottom-01.jpg` |
| bottom-02 | [File:Breeches (AM 1973.193-2).jpg](https://commons.wikimedia.org/wiki/File:Breeches_(AM_1973.193-2).jpg) | CC BY 4.0 | not researched (Auckland Museum) | `base/bottom-02.jpg` |
| bottom-03 | [File:Breeches (AM 1973.193-3).jpg](https://commons.wikimedia.org/wiki/File:Breeches_(AM_1973.193-3).jpg) | CC BY 4.0 | not researched (Auckland Museum) | `base/bottom-03.jpg` |
| bottom-04 | [File:Breeches (AM 1973.193-4).jpg](https://commons.wikimedia.org/wiki/File:Breeches_(AM_1973.193-4).jpg) | CC BY 4.0 | not researched (Auckland Museum) | `base/bottom-04.jpg` |
| bottom-05 | [File:Breeches (AM 1973.193-5).jpg](https://commons.wikimedia.org/wiki/File:Breeches_(AM_1973.193-5).jpg) | CC BY 4.0 | not researched (Auckland Museum) | `base/bottom-05.jpg` |
| bottom-06 | [File:Breeches MET 1972.139.7 B.jpg](https://commons.wikimedia.org/wiki/File:Breeches_MET_1972.139.7_B.jpg) | CC0 | not stated | `base/bottom-06.jpg` |
| outer-01 | [File:Canadienne civile ayant appartenu à Bertrand Branet, 2002.2151(3).jpg](https://commons.wikimedia.org/wiki/File:Canadienne_civile_ayant_appartenu_%C3%A0_Bertrand_Branet,_2002.2151(3).jpg) | CC0 | Anonymous | `base/outer-01.jpg` |
| shoe-01 | [File:78 chelsea boots.jpg](https://commons.wikimedia.org/wiki/File:78_chelsea_boots.jpg) | CC BY 4.0 | Free open sources aholaS | `base/shoe-01.jpg` |
| shoe-02 | [File:Goodyear sneakers, 1890s … DSC00745-001.JPG](https://commons.wikimedia.org/wiki/File:Goodyear_sneakers,_1890s,_Goodyear_Rubber_Manufacturing_Company_-_Bata_Shoe_Museum_-_DSC00745-001.JPG) | CC0 | Daderot | `base/shoe-02.jpg` |
| shoe-03 | [File:Michael Kors Kristy Lace Up Sneaker 43S4KRFS2E … (16286299927).jpg](https://commons.wikimedia.org/wiki/File:Michael_Kors_Kristy_Lace_Up_Sneaker_43S4KRFS2E_Kalbsleder_braun_(1)_(16286299927).jpg) | CC BY 2.0 | SPERA.de Designerschuhe, Taschen und Accessoires | `base/shoe-03.jpg` |
| top-01 | [File:Guards Fest 2024 (53479474703).jpg](https://commons.wikimedia.org/wiki/File:Guards_Fest_2024_(53479474703).jpg) | CC BY 2.0 | Erik Drost | `base/top-01.jpg` |
| top-02 | [File:T-shirt2.jpg](https://commons.wikimedia.org/wiki/File:T-shirt2.jpg) | Public domain | Elkagye | `base/top-02.jpg` |

## Duplicate-pair files and what each one derives from

`exact/` — byte-identical copies, licence unchanged from the base they copy:

| file | byte-identical copy of |
|---|---|
| `exact/exact-01.jpg` | `base/bottom-03.jpg` |
| `exact/exact-02.jpg` | `base/bottom-06.jpg` |
| `exact/exact-03.jpg` | `base/shoe-01.jpg` |
| `exact/exact-04.jpg` | `base/top-01.jpg` |

`variant/` — transformed, so a *derivative work* of the base (same licence and attribution as
the base, plus the note that it is re-encoded, rescaled, cropped, padded or brightened):

| file | transform of |
|---|---|
| `variant/near-01.jpg` | `base/bottom-01.jpg` |
| `variant/near-02.jpg` | `base/bottom-02.jpg` |
| `variant/near-03.jpg` | `base/bottom-04.jpg` |
| `variant/near-04.jpg` | `base/bottom-05.jpg` |
| `variant/near-05.jpg` | `base/shoe-03.jpg` |
| `variant/near-06.jpg` | `base/outer-01.jpg` |

"not researched" in the author column is copied from `evals/golden-provisional/CREDITS.md`,
which records the Commons entry as-is rather than guessing a name. Attribution text is the
author field exactly as Commons reports it; if a reuser needs a canonical citation, use the
Commons file page above, not this table.
