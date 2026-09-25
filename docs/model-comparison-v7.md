# Model comparison: Haiku 4.5 vs Sonnet 5 (v7 §A2)

Run on 2026-09-26 against one real batch (9 listing groups, apparel, beta seller `printeescustomshirts`, batch `46b141b2`), with the v7 prompts (several themes, describe-don't-transcribe, trademark filter on). Nothing was saved to the seller's batch. Each configuration ran the image analysis and the listing text on the same model. Costs are Anthropic list prices from the app's price table (Haiku 4.5 $1/$5, Sonnet 5 $2/$10 per million input/output tokens), including every retry.

## Summary

| Configuration | Listings that passed | Needed a retry | Cost for 9 | Per listing | Per listing that passed |
|---|---|---|---|---|---|
| Haiku 4.5 | 8/9 | 5 | $0.0839 | $0.0093 | $0.0105 |
| Sonnet 5 (thinking off) | 9/9 | 1 | $0.1703 | $0.0189 | $0.0189 |
| Sonnet 5 (adaptive, low) | 9/9 | 2 | $0.1808 | $0.0201 | $0.0201 |

**Read this with one caveat: the sample is not representative.** Almost every design in this batch shows Disney characters (Minnie and Mickey Mouse, Winnie the Pooh, The Lion King, the castle and parks). Haiku tends to name them; the trademark filter then rejects the listing. Sonnet describes them generically instead ("mouse character", "cartoon bear", "theme park"), so it passes the filter while the image itself is still Disney artwork. The pass rates above therefore say more about trademark handling than about title quality on original designs. A second run on a batch of original designs is needed before the model decision.

A bug in the new multi-theme check (it demanded a trademark theme such as "disney" in the title, which the filter forbids) failed three Haiku listings in the first run. It is fixed; the Haiku figures above are from the rerun after the fix.

### Observations

- **Title specificity.** Sonnet's titles name concrete, searchable things (hot cocoa, gingerbread, theme park snacks, holiday road trip, jungle lions). Haiku's lean on generic phrases ("Festive Holiday Tee", "Whimsical Winter Graphic", "Christmas Character Sweatshirt").
- **Themes.** Both return several themes. Haiku often lists brand names or vague words ("humor", "holiday", "festive") as themes; Sonnet lists subjects a buyer would search.
- **Validation.** Haiku's remaining failure and most of its retries were tags over 20 characters. Sonnet needed one retry across nine with thinking off.
- **Cost.** Sonnet 5 costs about twice as much per listing (about 1 cent more). Adaptive thinking at low effort added little cost and no visible quality gain here.

## Every listing, side by side

### `a/br6333-minnie-COMFORT`

**Haiku 4.5**: ok, 2 attempt(s), $0.0110. Themes: christmas, disney
- Title (116): Comfort Colors® Christmas Character Sweatshirt, Festive Holiday Tee, Cozy Winter Crewneck, Whimsical Caroling Design
- Tags: christmas sweatshirt, holiday tee, festive christmas, winter clothing, cozy crewneck, character shirt, holiday caroling, christmas lover, seasonal apparel, printed tee, gift for her, winter sweatshirt, holiday sweater

**Sonnet 5 (thinking off)**: ok, 1 attempt(s), $0.0274. Themes: christmas, mouse character, holiday cocoa
- Title (130): Comfort Colors® Christmas Mouse Sweatshirt, Cute Holiday Hot Cocoa Tee, Festive Winter Shirt for Women, Gingerbread Christmas Gift
- Tags: christmas sweatshirt, holiday mouse shirt, hot cocoa tee, cute christmas tee, gingerbread shirt, festive tee women, xmas gift for her, holiday apparel, winter sweatshirt, cute mouse design, christmas tee girls, cozy holiday shirt, hoodie christmas

**Sonnet 5 (adaptive, low)**: ok, 1 attempt(s), $0.0289. Themes: christmas, mouse character, holiday cheer
- Title (118): Comfort Colors® Cute Christmas Mouse Sweatshirt, Cozy Holiday Cartoon Tee, Cocoa Mouse Shirt for Women, Cute Xmas Gift
- Tags: christmas sweatshirt, cartoon mouse shirt, holiday cheer tee, cute xmas gift, womens christmas tee, cocoa mouse design, festive apparel, holiday cartoon, winter sweatshirt, gift for her, cute holiday shirt, christmas gift idea, sweatshirt

### `a/br6339`

**Haiku 4.5**: ok, 2 attempt(s), $0.0110. Themes: christmas, disney
- Title (122): Christmas Character Snowflake Shirt, Festive Holiday Tee, Winter Graphic Sweatshirt, Character Collage Gift, Xmas Crewneck
- Tags: christmas shirt, snowflake tee, holiday tee, character shirt, winter sweatshirt, festive apparel, xmas graphic tee, holiday gift, character collage, festive sweatshirt, winter holiday, christmas gift, graphic sweatshirt

**Sonnet 5 (thinking off)**: ok, 1 attempt(s), $0.0174. Themes: christmas, cartoon characters, winter, snowflake
- Title (115): Christmas Snowflake Shirt, Classic Cartoon Holiday Tee, Winter Snowflake Sweatshirt, Retro Animated Characters Gift
- Tags: christmas shirt, snowflake tee, cartoon holiday gift, winter sweatshirt, santa hat design, holiday family shirt, classic cartoon fan, xmas graphic tee, distressed christmas, mouse ears shirt, retro cartoon gift, winter holiday tee, holiday sweatshirt

**Sonnet 5 (adaptive, low)**: ok, 1 attempt(s), $0.0169. Themes: christmas, cartoon characters, snowflake, winter holiday
- Title (136): Christmas Cartoon Snowflake Shirt, Retro Animated Characters Tee, Holiday Snowflake Sweatshirt, Winter Xmas Gift, Cute Cartoon Fan Shirt
- Tags: christmas shirt, cartoon snowflake, holiday tee, winter sweatshirt, santa hat shirt, xmas gift shirt, cartoon fan gift, retro cartoon tee, festive snowflake, holiday season tee, christmas sweatshirt, winter holiday gift, cute christmas tee

### `a/br6341`

**Haiku 4.5**: ok, 1 attempt(s), $0.0072. Themes: christmas, food, humor
- Title (122): Christmas Snacking T-Shirt, Holiday Food Graphic Tee, Festive Treat Shirt, Winter Christmas Gift, Funny Food Lover Apparel
- Tags: christmas shirt, holiday food tee, snacking humor, festive graphic tee, winter holiday shirt, christmas gift, food lover gift, colorful holiday tee, christmas apparel, festive snacks, t-shirt, holiday treats, fun christmas tee

**Sonnet 5 (thinking off)**: ok, 1 attempt(s), $0.0175. Themes: theme park snacks, christmas, holiday travel, foodie
- Title (135): Christmas Theme Park Snacks Shirt, Holiday Foodie Tee, Theme Park Vacation Sweatshirt, Mouse Ear Wreath Gift, Cute Christmas Travel Top
- Tags: theme park shirt, holiday snacks tee, christmas sweatshirt, theme park gift, foodie christmas, mouse ears shirt, holiday travel tee, park snacks lover, xmas vacation tee, christmas wreath tee, park foodie gift, winter park shirt, cute holiday tee

**Sonnet 5 (adaptive, low)**: ok, 1 attempt(s), $0.0171. Themes: christmas, theme park snacks, holiday travel, food and drink
- Title (135): Christmas Theme Park Shirt, Holiday Snack Wreath Tee, Festival Food Lover Gift, Epcot Inspired Sweatshirt, World Showcase Christmas Tee
- Tags: theme park shirt, holiday snack tee, christmas wreath tee, foodie gift shirt, festival food shirt, theme park lover, holiday travel tee, snack lover gift, world showcase tee, christmas tee, park foodie shirt, holiday sweatshirt, xmas gift shirt

### `a/br6348`

**Haiku 4.5**: ok, 2 attempt(s), $0.0110. Themes: christmas, Disney
- Title (122): Christmas Main Street Sweatshirt, Festive Cartoon Characters Tee, Retro Holiday Graphic Shirt, Nostalgic Christmas Sweater
- Tags: christmas sweatshirt, retro christmas, festive shirt, holiday sweater, cartoon characters, christmas gift, nostalgic holiday, classic christmas, winter clothing, sweatshirt, holiday graphic tee, cozy christmas, vintage holiday

**Sonnet 5 (thinking off)**: ok, 2 attempt(s), $0.0243. Themes: christmas, cartoon mice characters, holiday road trip
- Title (117): Vintage Christmas Cartoon Shirt, Retro Holiday Road Trip Tee, Classic Cartoon Mouse Sweatshirt, Main Street Xmas Gift
- Tags: christmas shirt, holiday tee, cartoon mouse gift, retro christmas tee, vintage xmas shirt, xmas sweatshirt, road trip christmas, classic cartoon fan, festive winter tee, holiday family shirt, main street xmas, cartoon lovers gift, winter cartoon shirt

**Sonnet 5 (adaptive, low)**: ok, 2 attempt(s), $0.0229. Themes: christmas, cartoon mouse characters, holiday road trip
- Title (135): Retro Christmas Cartoon Shirt, Holiday Road Trip Tee, Classic Cartoon Mouse Sweatshirt, Vintage Xmas Gift, Main Street Holiday Crewneck
- Tags: christmas shirt, holiday road trip, cartoon mouse tee, retro christmas tee, vintage xmas gift, holiday cartoon fan, classic cartoon tee, xmas sweatshirt, holiday lights tee, main street xmas, winter cartoon shirt, festive road trip, cartoon fan gift

### `a/br6351-COMFORT`

**Haiku 4.5**: ok, 1 attempt(s), $0.0072. Themes: christmas, holiday
- Title (124): Comfort Colors® Christmas T-Shirt, Festive Holiday Tee, Whimsical Winter Graphic, Christmas Lover Gift, Holiday Season Shirt
- Tags: christmas shirt, holiday tee, festive graphic tee, winter christmas, holiday season, xmas gift, women's tee, christmas gift, holiday apparel, festive tshirt, christmas lover, cozy holiday, graphic shirt

**Sonnet 5 (thinking off)**: ok, 1 attempt(s), $0.0172. Themes: christmas, theme park treats, mouse ears
- Title (121): Comfort Colors® Christmas Wreath Shirt, Theme Park Treats Tee, Mouse Ears Holiday Sweatshirt, Festive Winter Gift for Her
- Tags: christmas shirt, theme park tee, mouse ears xmas, holiday wreath, festive sweatshirt, xmas gift for her, peppermint sweets, gingerbread cookie, hot cocoa graphic, candy cane shirt, winter holiday tee, cute christmas top, park snacks shirt

**Sonnet 5 (adaptive, low)**: ok, 1 attempt(s), $0.0185. Themes: christmas, theme park treats, gingerbread, holiday snacks
- Title (120): Comfort Colors® Christmas Theme Park Shirt, Gingerbread Mouse Ears Tee, Holiday Snacks Sweatshirt, Theme Park Lover Gift
- Tags: theme park shirt, christmas shirt, gingerbread tee, mouse ears shirt, holiday snacks, theme park gift, christmas wreath, hot cocoa tee, holiday sweatshirt, theme park lover, winter holiday tee, christmas treats, park fan gift

### `a/br6353-COMFORT`

**Haiku 4.5**: ok, 1 attempt(s), $0.0072. Themes: christmas, humorous
- Title (125): Comfort Colors® Christmas Snack Pun Shirt, Funny Holiday Tee, Santa Humor Gift, Winter Party Sweatshirt, Playful Xmas Graphic
- Tags: christmas shirt, funny holiday tee, santa pun, snack humor, christmas gift, winter humor, holiday party shirt, cartoon santa, playful christmas, xmas graphic tee, holiday sweatshirt, funny christmas, casual tee

**Sonnet 5 (thinking off)**: ok, 1 attempt(s), $0.0167. Themes: christmas, mouse, holiday humor
- Title (134): Comfort Colors® Christmas Mouse Shirt, Funny Santa Mouse Tee, Festive Cartoon Holiday Sweatshirt, Cute Winter Gift, Novelty Xmas Shirt
- Tags: christmas shirt, holiday tee, mouse graphic tee, santa mouse shirt, festive cartoon tee, cute christmas gift, funny holiday shirt, winter sweatshirt, novelty xmas tee, cartoon animal shirt, holiday humor tee, cheese lover gift, christmas mouse

**Sonnet 5 (adaptive, low)**: ok, 2 attempt(s), $0.0241. Themes: christmas, mouse cartoon character, holiday humor
- Title (133): Comfort Colors® Christmas Mouse Sweatshirt, Cute Holiday Cartoon Tee, Funny Christmas Shirt, Cheese Lover Gift, Winter Holiday Hoodie
- Tags: christmas sweatshirt, mouse cartoon, holiday humor tee, cute christmas gift, santa mouse shirt, winter holiday tee, funny holiday shirt, cartoon mouse shirt, christmas hoodie, cheese lover gift, holiday humor, cute cartoon shirt, xmas tee

### `a/br6355-COMFORT`

**Haiku 4.5**: failed, 2 attempt(s), $0.0115. Themes: disney, mickey mouse, minnie mouse, castle, whimsical
- Rejected: tag exceeds 20 characters: 'romantic couple shirt'; tag exceeds 20 characters: 'whimsical graphic tee'

**Sonnet 5 (thinking off)**: ok, 1 attempt(s), $0.0171. Themes: mouse ears christmas castle design, christmas, family vacation, holiday castle
- Title (132): Comfort Colors® Christmas Castle Shirt, Mouse Ears Holiday Tee, Theme Park Family Vacation Sweatshirt, Magical Castle Christmas Gift
- Tags: christmas shirt, mouse ears tee, castle sweatshirt, theme park shirt, family vacation tee, holiday castle, magical christmas, park fan gift, winter holiday tee, santa mouse shirt, christmas sweatshirt, holiday castle tee, theme park fan

**Sonnet 5 (adaptive, low)**: ok, 1 attempt(s), $0.0176. Themes: christmas, mouse ears, theme park castle, holiday couple
- Title (124): Comfort Colors® Christmas Castle Mouse Ears Shirt, Holiday Couple Sweatshirt, Theme Park Christmas Tee, Winter Vacation Gift
- Tags: theme park shirt, mouse ears tee, christmas couple, holiday sweatshirt, park fan gift, festive castle, winter vacation, christmas tee, string lights art, holiday matching, santa mouse, park christmas, cute holiday tee

### `a/br6356`

**Haiku 4.5**: ok, 2 attempt(s), $0.0105. Themes: christmas, winnie the pooh, holiday
- Title (124): Christmas Bear Sweatshirt, Holiday Lights Tee, Festive Character Crewneck, Christmas Gift for Families, Winter Holiday Shirt
- Tags: christmas sweatshirt, holiday lights, festive tee, christmas gift, character shirt, family holiday, winter sweatshirt, cozy crewneck, christmas tee, festive sweater, holiday character, cartoon bear, holiday themed

**Sonnet 5 (thinking off)**: ok, 1 attempt(s), $0.0161. Themes: christmas, cartoon bear, holiday lights
- Title (120): Christmas Bear Sweatshirt, Cute Cartoon Bear Tee, Holiday Lights Graphic Shirt, Festive Winter Crewneck, Santa Bear Gift
- Tags: christmas sweatshirt, cartoon bear shirt, holiday lights tee, santa bear gift, festive winter tee, xmas crewneck, cute christmas tee, holiday cheer shirt, winter holiday gift, christmas apparel, bear lover gift, candy cane shirt, holiday season tee

**Sonnet 5 (adaptive, low)**: ok, 1 attempt(s), $0.0161. Themes: christmas, bear character, holiday lights
- Title (130): Christmas Bear Sweatshirt, Cute Holiday Lights Tee, Cartoon Bear Christmas Shirt, Candy Cane Winter Gift, Festive Holiday Crewneck
- Tags: christmas bear, holiday lights tee, cartoon bear shirt, xmas sweatshirt, candy cane gift, winter holiday tee, festive crewneck, cute bear gift, holiday cheer, santa hat bear, christmas sweatshirt, cozy winter shirt, bear lover gift

### `a/br6357-COMFORT`

**Haiku 4.5**: ok, 1 attempt(s), $0.0072. Themes: christmas, nostalgia, animation
- Title (136): Comfort Colors® Retro Christmas Shirt, Nostalgic Holiday Tee, Vintage Animated Characters, Festive Winter Graphic, Classic Xmas Crewneck
- Tags: christmas shirt, retro holiday tee, vintage animated, nostalgic christmas, holiday graphic tee, animated characters, festive winter shirt, classic xmas tee, retro sweatshirt, ugly christmas, holiday nostalgia, cozy winter tee, crewneck sweatshirt

**Sonnet 5 (thinking off)**: ok, 1 attempt(s), $0.0166. Themes: christmas, lions, animated movie characters, jungle animals
- Title (138): Comfort Colors® Jungle Animal Christmas Shirt, Lion Movie Fan Tee, Cute Holiday Sweatshirt, Animated Animals Xmas Gift, Winter Cartoon Tee
- Tags: christmas shirt, jungle animals, lion fan gift, holiday sweatshirt, cartoon christmas, xmas tee, animal movie fan, santa hat animals, winter holiday tee, cute christmas gift, festive shirt, kids christmas tee, gift for fans

**Sonnet 5 (adaptive, low)**: ok, 1 attempt(s), $0.0186. Themes: christmas, lions, jungle animals, holiday movie characters
- Title (125): Comfort Colors® Jungle Lions Christmas Sweatshirt, Festive Animal Movie Tee, Holiday Gift for Fans, Cute Winter Cartoon Shirt
- Tags: christmas sweatshirt, jungle lion fans, holiday movie tee, festive animal shirt, xmas gift idea, cartoon christmas, winter holiday tee, santa hat lions, animal lovers gift, holiday party shirt, cute jungle animals, christmas tree shirt, sweatshirt

