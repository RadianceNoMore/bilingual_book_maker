# Protected-name terminology audit

**Audit date:** 2026-11-07  
**Book:** *A Certain Unscientific Romantic Comedy*  
**EPUB:** `output/Railgun, A Certain Unscientific Romantic Comedy_bilingual.epub`

## Result

I audited the completed EPUB's 171 protected-name labels against the source context, official Toaru franchise pages, and licensed English publisher/distributor terminology. I changed 28 mappings covering 1,161 marked occurrences:

- 13 two-part Japanese personal names were put in **surname-first** order (856 occurrences), following the user's instruction.
- 15 incorrect identity, spelling, term, or name-form mappings were corrected (305 occurrences).

The catalogue still has 171 labels: 170 mapped and one pending label with zero occurrences. No Gemini requests were needed. The update changed the catalogue targets and regenerated the EPUB's name stylesheet; it did not alter translated prose.

## Japanese names reordered

| Source label | Previous target | Updated target | Occurrences |
|---|---|---|---:|
| `上条当麻` | Touma Kamijou | Kamijou Touma | 88 |
| `介旅初矢` | Hatsuya Kaitabi | Kaitabi Hatsuya | 15 |
| `佐天泪子` | Ruiko Saten | Saten Ruiko | 438 |
| `初春饰利` | Kazari Uiharu | Uiharu Kazari | 180 |
| `后藤一里` | Hitori Gotoh | Gotoh Hitori | 14 |
| `婚后光子` | Mitsuko Kongou | Kongou Mitsuko | 52 |
| `布束砥信` | Shinobu Nunotaba | Nunotaba Shinobu | 21 |
| `帆风润子` | Junko Hokaze | Hokaze Junko | 19 |
| `御坂嘉音` | Kanon Misaka | Misaka Kanon | 1 |
| `御坂美玲` | Misuzu Misaka | Misaka Misuzu | 2 |
| `结标淡希` | Awaki Musujime | Musujime Awaki | 22 |
| `结标炎希` | Enki Musujime | Musujime Enki | 1 |
| `福泽谕吉` | Yukichi Fukuzawa | Fukuzawa Yukichi | 3 |

Other Japanese full names already in surname-first order were left as-is, including Misaka Mikoto, Shirai Kuroko, Kiyama Harumi, Shokuhou Misaki, Wannai Kinuho, and Awatsuki Maaya. Single-name forms such as Mikoto, Misaki, and Kuroko were also left as they were.

## Incorrect or inconsistent targets corrected

| Source label | Previous target | Updated target | Occurrences | Reason |
|---|---|---|---:|---|
| `Judgement` | Judgement | Judgment | 1 | Standardized to the one-e spelling used on the official Seven Seas Railgun series page. Seven Seas uses both spellings in different copy, so this is an explicit consistency choice. |
| `上条先生` | Mr. Kamijo | Mr. Kamijou | 1 | Corrected the licensed English spelling of Kamijou. |
| `初春` | Uihoro | Uiharu | 79 | Corrected a misspelling of Uiharu. |
| `固法` | Kurofuku | Konori | 1 | Corrected the character's surname. |
| `固法前辈` | Kuwama-senpai | Konori-senpai | 2 | Corrected the surname and retained the senpai address. |
| `固法美伟` | Mitori Konori | Konori Mii | 27 | Corrected the given-name reading and used Japanese surname-first order. The official character page gives the reading `このり みい`. |
| `重福省帆` | Miyamae Saiai | Juufuku Miho | 24 | Replaced a different character's name with the official reading `じゅうふく みほ`, surname first. |
| `警策看取` | Kischukami Misaka | Kouzaku Mitori | 2 | Replaced the incorrect name with the official romanization. |
| `才人工坊` | Mental Out | Clone Dolly | 1 | The official character page identifies the institution as `クローンドリー` (Clone Dolly). Mental Out is Shokuhou's ability, not this institution. |
| `食蜂酱` | Misaki-chan | Shokuhou-chan | 4 | The source uses Shokuhou's surname plus an affectionate suffix; the old target changed it to her given name. |
| `番外个体` | Worst | Misaka Worst | 1 | Restored the full official English character name. |
| `御坂妹妹` | Misaka Sister | Sister | 155 | Used the licensed singular term for one clone; “Sisters” is the collective plural. |
| `蓝发耳环` | Blue Hair Pierce | Aogami Pierce | 1 | Used the official franchise romanization, in Japanese name order. |
| `虚空爆炸` | Riot | Graviton Bomb | 4 | The story context is the Graviton Bomb case; “Riot” names the wrong event. |
| `幻想御主` | Fantasy Master | Imagine Master | 2 | Matched the source book's own parenthetical English rendering in its contents entry. |

## Source references and terminology choices

- The official [Railgun T character pages](https://toaru-project.com/railgun_t/chara/uiharu.html), [Saten](https://toaru-project.com/railgun_t/chara/saten.html), [Kongou](https://toaru-project.com/railgun_t/chara/kongou.html), [Hokaze](https://toaru-project.com/railgun_t/chara/hokaze.html), [Kamijou](https://toaru-project.com/railgun_t/chara/kamijo.html), and [Kouzaku](https://toaru-project.com/railgun_t/chara/kouzaku.html) provide the romanizations used for the canonical names. The official older [Konori page](https://toaru-project.com/railgun/chara/12.html) gives her reading as `このり みい`; the [Juufuku page](https://toaru-project.com/railgun/chara/13.html) gives `じゅうふく みほ`. The official Kouzaku page also gives the Clone Dolly reading.
- The official [Index III character page for Aogami Pierce](https://toaru-project.com/index_3/chara/pierceaogami.html) uses the romanization “AOGAMI PIERCE.” This audit follows that Japanese-style form; licensed English editions may localize the nickname differently.
- Seven Seas' [Railgun series page](https://sevenseasentertainment.com/series/a-certain-scientific-railgun/) uses “Judgment.” Its [Volume 2 page](https://sevenseasentertainment.com/books/a-certain-scientific-railgun-vol-2/) spells it “Judgement”; the catalogue now uses one consistent spelling.
- Yen Press uses “Kamijou” in its official [Index Volume 1 page](https://yenpress.com/titles/9781975347529-a-certain-magical-index-vol-1-manga), “Sister”/“Sisters” in [Volume 6](https://yenpress.com/titles/9780316345996-a-certain-magical-index-vol-6-manga), and “Misaka Worst” in [New Testament Volume 1](https://yenpress.com/titles/9781975380663-a-certain-magical-index-nt-vol-1-light-novel).
- Seven Seas' [Railgun Volume 3 page](https://sevenseasentertainment.com/books/a-certain-scientific-railgun-vol-3/) confirms “Level Upper,” “AIM Burst,” and “Kiyama Harumi,” which were retained. The licensed [Apple TV episode description](https://tv.apple.com/ca/episode/everyone-likes-this-sort-of-thing/umc.cmc.3dv8uolho4zusuhm1e16p7ome?showId=umc.cmc.2lcck6kkyccjhcev1cvo8636y) calls the relevant plotline the “graviton bomb case,” supporting the corrected Graviton Bomb label.

## Validation

- Embedded catalogue validates: 171 labels, 170 mapped, one pending with count 0.
- Run remains `complete` across 78 source windows.
- EPUB ZIP integrity passes.
- Generated name-stylesheet rules were checked for all 28 updated mappings.
- A byte-identical pre-edit backup is at `output/terminology-audit-backup/Railgun_before_official_terminology_20261107.epub`.
