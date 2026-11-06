# evals/golden — SOURCING PLAN (not the golden set)

**This file is a sourcing plan only. It does NOT contain, and must not be mistaken for, the E0-T1 golden set.** Per `evals/golden/README.md` the golden set must be 40 **real** photos with 20 **hand-drawn** masks; synthetic stand-ins are forbidden. As of writing, `evals/golden/manifest.json` does not exist and the set is empty by design — every eval metric stays `UNMEASURED`.

**No photos or masks were fabricated or downloaded.** The `manifest.draft.json` companion is a *staged, contract-shaped* draft. Do **not** rename it to `manifest.json` until the real files are physically present in `photos/` and `masks/` — `validate_golden.py` treats a missing photo as a broken promise, not a placeholder.

## How to use this plan

1. For each slot, run the **search prompt** in a licensed stock library (e.g. a paid royalty-free source) or hand it to a photographer. The prompt is written to be paste-ready.
2. Save the accepted real photo to `evals/golden/photos/<id>.jpg` (or `.png`).
3. For the 20 slots flagged **MASK**, draw an 8-bit single-channel PNG (255 = garment, 0 = background) at the same pixel size, save to `evals/golden/masks/<id>.png`.
4. When all 40 photos + 20 masks exist, copy `manifest.draft.json` to `manifest.json` and run `make golden`.

## Composition & mask allocation

- 35 normal entries: **10 top, 10 bottom, 5 dress, 5 shoe, 5 accessory**.
- **5 hard cases** (`hard-01`…`hard-05`), each breaks a different naive pipeline.
- **20 hand masks**, allocated as: tops `top-01,04,07,09,10`; bottoms `bottom-02,04,06,09`; dresses `dress-02,04`; shoes `shoe-01,05`; accessories `acc-02,05`; **all 5 hard cases**. (All 5 hard cases are masked because measuring IoU on the breaking cases is the whole point; reallocate freely — `validate_golden.py` only checks the count is exactly 20.)

## Licensing note

Do not pull images from Google Images and commit them as golden photos: most are all-rights-reserved and the downloaded file will not actually match the precise brief (e.g. 'slight pilling at the cuffs', 'black knit on a black chair shot at night'). The existing `evals/golden-provisional/` set proves web sourcing cannot even satisfy the composition (17 entries, no dresses, **algorithmic** not hand masks) — it is explicitly *not* the golden set. Prefer: photos Bert shoots himself, or a paid royalty-free licence with the exact garment/background specified.

## TOPS (top-01 … top-10)

### `top-01` — MASK ✓

- **Filename:** `evals/golden/photos/top-01.jpg`
- **Search prompt (paste-ready):** `white cotton t-shirt flat lay plain light grey background soft daylight wrinkled hem`
- **Brief:** White cotton t-shirt, laid flat on plain light grey surface, diffuse daylight, full frame, slight wrinkles at hem.
- **Draft metadata:** category=`top`, pattern=`solid`, colors=['white'], material=['cotton']
- **hard_attributes (3):** ['slight wrinkles at the hem', 'ribbed neckline', 'no print or logo']

### `top-02` — no mask

- **Filename:** `evals/golden/photos/top-02.jpg`
- **Search prompt (paste-ready):** `charcoal knit sweater folded white table soft window light`
- **Brief:** Charcoal knit sweater folded on white table, soft window light, garment fills 80% of frame.
- **Draft metadata:** category=`top`, pattern=`knit`, colors=['charcoal'], material=['wool']
- **hard_attributes (3):** ['folded into a square', 'no visible shadow edge', 'charcoal reads near-grey on white']

### `top-03` — no mask

- **Filename:** `evals/golden/photos/top-03.jpg`
- **Search prompt (paste-ready):** `navy button down shirt wooden hanger pale wall sleeves hanging`
- **Brief:** Navy button-down shirt on wooden hanger against pale wall, sleeves hanging naturally, even soft lighting.
- **Draft metadata:** category=`top`, pattern=`solid`, colors=['navy'], material=['cotton']
- **hard_attributes (3):** ['sleeves hang naturally, not pinned', 'wooden hanger shows above collar', 'button placket slightly skewed']

### `top-04` — MASK ✓

- **Filename:** `evals/golden/photos/top-04.jpg`
- **Search prompt (paste-ready):** `black hoodie flat lay hood folded back off white background softbox`
- **Brief:** Black hoodie laid flat, hood folded back, plain off-white background, diffuse softbox light.
- **Draft metadata:** category=`top`, pattern=`solid`, colors=['black'], material=['cotton']
- **hard_attributes (3):** ['hood folded back over the shoulders', 'kangaroo pocket at centre', 'drawstrings resting asymmetric']

### `top-05` — no mask

- **Filename:** `evals/golden/photos/top-05.jpg`
- **Search prompt (paste-ready):** `breton striped top cream navy stripes flat lay beige backdrop`
- **Brief:** Striped Breton top flat-lay, cream and navy stripes, light beige fabric backdrop, even lighting.
- **Draft metadata:** category=`top`, pattern=`striped`, colors=['cream', 'navy'], material=['cotton']
- **hard_attributes (3):** ['cream and navy horizontal stripes', 'classic boat neckline', 'thin ~1cm stripe pitch']

### `top-06` — no mask

- **Filename:** `evals/golden/photos/top-06.jpg`
- **Search prompt (paste-ready):** `floral blouse on hanger white wall soft daylight small print`
- **Brief:** Floral blouse on hanger, busy small print, plain white wall, soft daylight.
- **Draft metadata:** category=`top`, pattern=`floral`, colors=['white', 'blue'], material=['cotton']
- **hard_attributes (3):** ['small busy floral print', 'shoulders dimpled on the hanger', 'sheer where backlit']

### `top-07` — MASK ✓

- **Filename:** `evals/golden/photos/top-07.jpg`
- **Search prompt (paste-ready):** `denim jacket open flat lay light grey background visible stitching pockets`
- **Brief:** Denim jacket laid flat open on light grey surface, natural diffuse light, visible stitching and pockets.
- **Draft metadata:** category=`top`, pattern=`solid`, colors=['blue'], material=['denim']
- **hard_attributes (3):** ['open flat, lapels splayed', 'visible topstitching on pockets', 'two chest patch pockets']

### `top-08` — no mask

- **Filename:** `evals/golden/photos/top-08.jpg`
- **Search prompt (paste-ready):** `olive green cardigan buttoned hanger cream wall soft lighting`
- **Brief:** Olive green cardigan buttoned on hanger against cream wall, soft even lighting.
- **Draft metadata:** category=`top`, pattern=`knit`, colors=['olive'], material=['wool']
- **hard_attributes (3):** ['buttoned centre placket', 'on a hanger, sleeves straight', 'ribbed cuffs and hem']

### `top-09` — MASK ✓

- **Filename:** `evals/golden/photos/top-09.jpg`
- **Search prompt (paste-ready):** `red graphic t-shirt faded print flat lay white pilling`
- **Brief:** Red graphic tee with faded print, laid flat on white surface, diffuse light, slight pilling visible.
- **Draft metadata:** category=`top`, pattern=`graphic`, colors=['red'], material=['cotton']
- **hard_attributes (3):** ['faded graphic print, low contrast', 'slight pilling under the arms', 'stretched neckline']

### `top-10` — MASK ✓

- **Filename:** `evals/golden/photos/top-10.jpg`
- **Search prompt (paste-ready):** `beige linen shirt sleeves rolled flat lay light wood window light`
- **Brief:** Beige linen shirt, sleeves rolled, flat-lay on light wood, soft window light, no colour cast.
- **Draft metadata:** category=`top`, pattern=`solid`, colors=['beige'], material=['linen']
- **hard_attributes (3):** ['sleeves rolled to the elbow', 'linen slubs and wrinkles', 'laid on light wood, warm tone']

## BOTTOMS (bottom-01 … bottom-10)

### `bottom-01` — no mask

- **Filename:** `evals/golden/photos/bottom-01.jpg`
- **Search prompt (paste-ready):** `blue denim jeans flat lay front view plain white background creases knees`
- **Brief:** Blue denim jeans laid flat front view on plain white surface, diffuse light, slight creases at knees.
- **Draft metadata:** category=`bottom`, pattern=`solid`, colors=['blue'], material=['denim']
- **hard_attributes (3):** ['front view, legs together', 'slight creases at the knees', 'belt loops and five pockets']

### `bottom-02` — MASK ✓

- **Filename:** `evals/golden/photos/bottom-02.jpg`
- **Search prompt (paste-ready):** `black leggings folded light grey backdrop fabric texture`
- **Brief:** Black leggings folded on light grey backdrop, soft even lighting, fabric texture visible.
- **Draft metadata:** category=`bottom`, pattern=`solid`, colors=['black'], material=['polyester']
- **hard_attributes (3):** ['folded in half at the waist', 'matte fabric, no sheen', 'wide elastic waistband']

### `bottom-03` — no mask

- **Filename:** `evals/golden/photos/bottom-03.jpg`
- **Search prompt (paste-ready):** `khaki chinos hanger pale wall diffuse daylight`
- **Brief:** Khaki chinos on hanger against pale wall, diffuse daylight, garment fills frame.
- **Draft metadata:** category=`bottom`, pattern=`solid`, colors=['khaki'], material=['cotton']
- **hard_attributes (3):** ['on a hanger by the waistband', 'flat front, no pleats', 'slight taper to the ankle']

### `bottom-04` — MASK ✓

- **Filename:** `evals/golden/photos/bottom-04.jpg`
- **Search prompt (paste-ready):** `pleated midi skirt flat lay white background visible folds waistband`
- **Brief:** Pleated midi skirt laid flat on white surface, soft light, folds and waistband visible.
- **Draft metadata:** category=`bottom`, pattern=`solid`, colors=['white'], material=['polyester']
- **hard_attributes (3):** ['knife pleats from waist to hem', 'wide waistband with closure', 'laid flat, panel overlaps']

### `bottom-05` — no mask

- **Filename:** `evals/golden/photos/bottom-05.jpg`
- **Search prompt (paste-ready):** `grey sweatpants flat lay light beige drawstring visible`
- **Brief:** Grey sweatpants laid flat on light beige background, diffuse softbox, drawstring visible.
- **Draft metadata:** category=`bottom`, pattern=`solid`, colors=['grey'], material=['cotton']
- **hard_attributes (3):** ['drawstring knotted at centre', 'ribbed ankle cuffs', 'baggy through the thigh']

### `bottom-06` — MASK ✓

- **Filename:** `evals/golden/photos/bottom-06.jpg`
- **Search prompt (paste-ready):** `tan suede trousers buttoned cuffs flat lay neutral grey museum lighting`
- **Brief:** Tan suede trousers with buttoned cuffs, flat-lay on neutral grey, museum-style documentation lighting.
- **Draft metadata:** category=`bottom`, pattern=`solid`, colors=['tan'], material=['suede']
- **hard_attributes (3):** ['buttoned cuffs at the hem', 'suede nap, soft sheen', 'creases pressed along the leg']

### `bottom-07` — no mask

- **Filename:** `evals/golden/photos/bottom-07.jpg`
- **Search prompt (paste-ready):** `plaid pajama bottoms flat lay white full length`
- **Brief:** Patterned plaid pajama bottoms laid flat on white, even light, full length in frame.
- **Draft metadata:** category=`bottom`, pattern=`plaid`, colors=['red', 'blue', 'green'], material=['cotton']
- **hard_attributes (3):** ['plaid check print', 'elastic waist with drawstring', 'full length, both legs flat']

### `bottom-08` — no mask

- **Filename:** `evals/golden/photos/bottom-08.jpg`
- **Search prompt (paste-ready):** `white linen trousers flat lay light wood soft window light wrinkles`
- **Brief:** White linen trousers flat-lay on light wood, soft window light, slight wrinkles.
- **Draft metadata:** category=`bottom`, pattern=`solid`, colors=['white'], material=['linen']
- **hard_attributes (3):** ['flat-lay, legs slightly apart', 'linen wrinkles throughout', 'rolled or unfinished hem']

### `bottom-09` — MASK ✓

- **Filename:** `evals/golden/photos/bottom-09.jpg`
- **Search prompt (paste-ready):** `black denim shorts hanger cream wall diffuse light`
- **Brief:** Black denim shorts on hanger against cream wall, diffuse light.
- **Draft metadata:** category=`bottom`, pattern=`solid`, colors=['black'], material=['denim']
- **hard_attributes (3):** ['on a hanger by the waistband', 'short inseam, frayed hem', 'five-pocket front']

### `bottom-10` — no mask

- **Filename:** `evals/golden/photos/bottom-10.jpg`
- **Search prompt (paste-ready):** `olive cargo pants flat lay pockets zips grey surface`
- **Brief:** Olive cargo pants laid flat, pockets and zips visible, on grey surface, even soft lighting.
- **Draft metadata:** category=`bottom`, pattern=`solid`, colors=['olive'], material=['cotton']
- **hard_attributes (3):** ['cargo pockets on both thighs', 'zip and snap closures', 'drawstring or button waist']

## DRESSES (dress-01 … dress-05)

### `dress-01` — no mask

- **Filename:** `evals/golden/photos/dress-01.jpg`
- **Search prompt (paste-ready):** `floral summer dress hanger white wall soft daylight full length`
- **Brief:** Floral summer dress on hanger against white wall, soft daylight, full length visible.
- **Draft metadata:** category=`dress`, pattern=`floral`, colors=['white', 'pink'], material=['cotton']
- **hard_attributes (3):** ['on a hanger, straps up', 'full length, skirt flares', 'small floral print']

### `dress-02` — MASK ✓

- **Filename:** `evals/golden/photos/dress-02.jpg`
- **Search prompt (paste-ready):** `black slip dress flat lay pale grey straps hem visible`
- **Brief:** Black slip dress laid flat on pale grey surface, diffuse light, straps and hem visible.
- **Draft metadata:** category=`dress`, pattern=`solid`, colors=['black'], material=['satin']
- **hard_attributes (3):** ['thin spaghetti straps', 'bias-cut, drapes asymmetrically', 'matte satin sheen']

### `dress-03` — no mask

- **Filename:** `evals/golden/photos/dress-03.jpg`
- **Search prompt (paste-ready):** `red midi dress hanger cream backdrop soft lighting drape`
- **Brief:** Red midi dress on hanger against cream backdrop, soft even lighting, fabric drape visible.
- **Draft metadata:** category=`dress`, pattern=`solid`, colors=['red'], material=['cotton']
- **hard_attributes (3):** ['midi length to the calf', 'on a hanger, gathered waist', 'soft drape, no structural seams']

### `dress-04` — MASK ✓

- **Filename:** `evals/golden/photos/dress-04.jpg`
- **Search prompt (paste-ready):** `denim shirt dress flat lay white buttons pockets visible`
- **Brief:** Denim shirt-dress flat-lay on white, buttons and pockets visible, diffuse softbox.
- **Draft metadata:** category=`dress`, pattern=`solid`, colors=['blue'], material=['denim']
- **hard_attributes (3):** ['button-through centre front', 'shirt collar and cuffs', 'patch pockets at the chest']

### `dress-05` — no mask

- **Filename:** `evals/golden/photos/dress-05.jpg`
- **Search prompt (paste-ready):** `knit sweater dress folded light beige ribbing texture`
- **Brief:** Knit sweater dress folded on light beige surface, texture and ribbing visible, soft light.
- **Draft metadata:** category=`dress`, pattern=`knit`, colors=['beige'], material=['wool']
- **hard_attributes (3):** ['folded, ribbed turtleneck', 'heavy knit texture', 'no waist seam, column shape']

## SHOES (shoe-01 … shoe-05)

### `shoe-01` — MASK ✓

- **Filename:** `evals/golden/photos/shoe-01.jpg`
- **Search prompt (paste-ready):** `white leather sneakers pair plain white surface soft light scuffs`
- **Brief:** White leather sneakers pair on plain white surface, soft light, slight scuffs visible.
- **Draft metadata:** category=`shoe`, pattern=`solid`, colors=['white'], material=['leather']
- **hard_attributes (3):** ['pair, laces untied', 'slight scuffs on the toe', 'rubber sole contrast']

### `shoe-02` — no mask

- **Filename:** `evals/golden/photos/shoe-02.jpg`
- **Search prompt (paste-ready):** `black chelsea boots pair grey backdrop elastic side gore`
- **Brief:** Black Chelsea boots pair on grey backdrop, diffuse light, elastic side gore visible.
- **Draft metadata:** category=`shoe`, pattern=`solid`, colors=['black'], material=['leather']
- **hard_attributes (3):** ['elastic side gore panels', 'pull tabs at the heel', 'pair, facing outward']

### `shoe-03` — no mask

- **Filename:** `evals/golden/photos/shoe-03.jpg`
- **Search prompt (paste-ready):** `brown suede loafers pair light wood window light`
- **Brief:** Brown suede loafers pair on light wood, soft window light.
- **Draft metadata:** category=`shoe`, pattern=`solid`, colors=['brown'], material=['suede']
- **hard_attributes (3):** ['penny slot across the vamp', 'low heel, no laces', 'pair, side by side']

### `shoe-04` — no mask

- **Filename:** `evals/golden/photos/shoe-04.jpg`
- **Search prompt (paste-ready):** `canvas espadrilles pair white surface even lighting`
- **Brief:** Canvas espadrilles pair on white surface, even lighting.
- **Draft metadata:** category=`shoe`, pattern=`solid`, colors=['natural', 'white'], material=['canvas']
- **hard_attributes (3):** ['jute rope sole', 'flat, no laces', 'pair, toes pointing in']

### `shoe-05` — MASK ✓

- **Filename:** `evals/golden/photos/shoe-05.jpg`
- **Search prompt (paste-ready):** `leather ankle boots laces pair grey surface creasing`
- **Brief:** Leather ankle boots with laces pair on grey surface, diffuse light, creasing visible.
- **Draft metadata:** category=`shoe`, pattern=`solid`, colors=['brown'], material=['leather']
- **hard_attributes (3):** ['lace-up front', 'creases across the vamp', 'pair, ankle height']

## ACCESSORIES (acc-01 … acc-05)

### `acc-01` — no mask

- **Filename:** `evals/golden/photos/acc-01.jpg`
- **Search prompt (paste-ready):** `black felt dress hat stand neutral backdrop brim crown visible`
- **Brief:** Black felt dress hat on a stand against neutral backdrop, soft light, brim and crown visible.
- **Draft metadata:** category=`accessory`, pattern=`solid`, colors=['black'], material=['felt']
- **hard_attributes (3):** ['on a hat stand, crown up', 'wide brim, dented crown', 'felt texture, no shine']

### `acc-02` — MASK ✓

- **Filename:** `evals/golden/photos/acc-02.jpg`
- **Search prompt (paste-ready):** `canvas tote bag printed logo dark purple backdrop handle shadow`
- **Brief:** Canvas tote bag printed logo on dark purple backdrop, handle shadow visible.
- **Draft metadata:** category=`accessory`, pattern=`graphic`, colors=['natural', 'blue'], material=['canvas']
- **hard_attributes (3):** ['screen-printed logo on the front', 'handle casts shadow on the cloth', 'bag sags, not full']

### `acc-03` — no mask

- **Filename:** `evals/golden/photos/acc-03.jpg`
- **Search prompt (paste-ready):** `red blue plaid wool belt open V shape grey surface`
- **Brief:** Red and blue plaid wool belt laid open in a V on grey surface, soft light.
- **Draft metadata:** category=`accessory`, pattern=`plaid`, colors=['red', 'blue'], material=['wool']
- **hard_attributes (3):** ['laid open in a V shape', 'buckle at one end', 'wool tartan weave']

### `acc-04` — no mask

- **Filename:** `evals/golden/photos/acc-04.jpg`
- **Search prompt (paste-ready):** `black acetate sunglasses red fabric cast shadow diffuse light`
- **Brief:** Black acetate sunglasses on red fabric, cast shadow visible, diffuse light.
- **Draft metadata:** category=`accessory`, pattern=`solid`, colors=['black', 'red'], material=['acetate']
- **hard_attributes (3):** ['on red fabric, not a face', 'cast shadow under the bridge', 'folded arms, lenses up']

### `acc-05` — MASK ✓

- **Filename:** `evals/golden/photos/acc-05.jpg`
- **Search prompt (paste-ready):** `leather crossbody bag flap open white surface soft lighting`
- **Brief:** Leather crossbody bag flap open on white surface, soft even lighting.
- **Draft metadata:** category=`accessory`, pattern=`solid`, colors=['brown'], material=['leather']
- **hard_attributes (3):** ['flap open, interior visible', 'long adjustable strap coiled', 'on white, soft shadow']

## HARD CASES (hard-01 … hard-05)

### `hard-01` — MASK ✓  **HARD CASE**

- **Filename:** `evals/golden/photos/hard-01.jpg`
- **Search prompt (paste-ready):** `black knit sweater on black chair low light night phone photo`
- **Brief:** Black knit sweater on a black chair, shot on a phone at night, low light, garment blends into background. (breaks colour-based segmentation)
- **Draft metadata:** category=`top`, pattern=`knit`, colors=['black'], material=['wool']
- **hard_attributes (3):** ['black garment on black chair, no edge', 'low-light phone photo with noise', 'blends by colour, not shape']

### `hard-02` — MASK ✓  **HARD CASE**

- **Filename:** `evals/golden/photos/hard-02.jpg`
- **Search prompt (paste-ready):** `patterned floral dress on busy floral bedspread phone indoor light`
- **Brief:** Patterned dress on a busy floral bedspread, garment and background share colours, phone photo, indoor light. (breaks background subtraction)
- **Draft metadata:** category=`dress`, pattern=`floral`, colors=['pink', 'green'], material=['cotton']
- **hard_attributes (3):** ['floral dress on floral bedding, colours match', 'background subtraction fails', 'phone indoor light, soft']

### `hard-03` — MASK ✓  **HARD CASE**

- **Filename:** `evals/golden/photos/hard-03.jpg`
- **Search prompt (paste-ready):** `white shirt wire hanger cluttered bedroom window backlight high contrast`
- **Brief:** White shirt on a wire hanger against a cluttered bedroom, strong window backlight, high contrast. (breaks silhouette/backlight handling)
- **Draft metadata:** category=`top`, pattern=`solid`, colors=['white'], material=['cotton']
- **hard_attributes (3):** ['wire hanger, thin outline', 'strong window backlight, silhouette only', 'cluttered bedroom behind']

### `hard-04` — MASK ✓  **HARD CASE**

- **Filename:** `evals/golden/photos/hard-04.jpg`
- **Search prompt (paste-ready):** `denim jacket sleeve crossing body patterned rug phone uneven light`
- **Brief:** Denim jacket with a sleeve crossing over the body on a patterned rug, phone photo, uneven lighting. (breaks occlusion/self-overlap)
- **Draft metadata:** category=`outerwear`, pattern=`solid`, colors=['blue'], material=['denim']
- **hard_attributes (3):** ['one sleeve crosses the torso', 'patterned rug behind, low contrast', 'phone photo, uneven light']

### `hard-05` — MASK ✓  **HARD CASE**

- **Filename:** `evals/golden/photos/hard-05.jpg`
- **Search prompt (paste-ready):** `navy trousers draped dark sofa strap crossing cluttered living room phone flash`
- **Brief:** Navy trousers draped over a dark sofa with a strap crossing the garment, cluttered living room, phone flash. (breaks strap/occlusion + flash)
- **Draft metadata:** category=`bottom`, pattern=`solid`, colors=['navy'], material=['cotton']
- **hard_attributes (3):** ['draped over dark sofa, low contrast', 'strap crosses the garment', 'phone flash blowout on one side']
