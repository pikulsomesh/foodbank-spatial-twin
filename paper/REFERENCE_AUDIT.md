# Reference audit (2026-10-26)

Every entry in `refs.bib` and every external fact or link in the README, wiki, cost page, operations data, LinkedIn post and CTFB email draft was checked against its primary source on 2026-10-26. Where a source could not be checked, the reference and the claim that relied on it were removed. No replacement facts were invented.

Status key: **OK** verified as cited; **Fixed** source is real but the metadata or the claim was wrong and has been corrected; **Removed** could not be audited, so it was dropped.

## refs.bib

| Ref | Status | Evidence | Fix |
|---|---|---|---|
| grieves2017digital | OK | Crossref DOI 10.1007/978-3-319-38756-7_4 | none |
| kritzinger2018digital | OK | Crossref DOI 10.1016/j.ifacol.2018.08.474 | none |
| tao2019digital | OK | Crossref DOI 10.1109/TII.2018.2873186 | none |
| fuller2020digital | OK | Crossref DOI 10.1109/ACCESS.2020.2998358 | none |
| kerbl20233d | Fixed | ACM TOG 42(4), article 139 | pages set to 139:1--139:14 |
| mildenhall2020nerf | Fixed | ECCV 2020, Springer LNCS, pp. 405--421 | booktitle, series, publisher, pages |
| schoenberger2016sfm | Fixed | CVPR 2016, pp. 4104--4113 | pages and DOI 10.1109/CVPR.2016.445 |
| wang2024dust3r | Fixed | CVPR 2024, pp. 20697--20709 | pages and DOI 10.1109/CVPR52733.2024.01956 |
| ha2018world | Fixed | NeurIPS 31 (2018), papers.nips.cc | venue and URL |
| brooks2024video | Fixed | OpenAI technical report, 2024-02-15 | author list, date, openai.com/index URL |
| worldlabs_marble | Fixed | docs.worldlabs.ai quickstart | real page title "World API quickstart" |
| worldlabs_multiimage | Fixed (new) | docs.worldlabs.ai multi-image prompt guide | added to support the "up to eight images" claim; the "eight with reconstruction" wording was dropped |
| worldlabs_spz | Fixed | docs.worldlabs.ai SPZ rendering page | real page title |
| spz | OK | github.com/nianticlabs/spz | none |
| spark | OK | sparkjs.dev / World Labs | none |
| usdconvertgsplat | Fixed | pypi project page, NVIDIA | author and URL |
| openusd | Fixed | openusd.org "Introduction to USD" | now cites Pixar's intro page |
| aousd | Fixed (new) | aousd.org | supports the "open standard" sentence |
| aousd2603 | Fixed | AOUSD announcement, 2026-03-24 | real title and date |
| usd_gsplat_schema | OK | OpenUSD ParticleField3DGaussianSplat schema page | none |
| usd_particle_position | Fixed (new) | ParticleFieldPositionAttributeAPI page | supports the half-precision `positionsh` sentence |
| omniverse | Fixed | NVIDIA Omniverse page | title; the paper's description now matches NVIDIA's ("libraries that bring OpenUSD interoperability, RTX rendering and physics simulation into applications") |
| kit_app_template | OK | github.com/NVIDIA-Omniverse/kit-app-template | none |
| blender | Fixed | docs.blender.org Python API | now cites the API docs; Blender 4.5 version notes dropped as irrelevant |
| awssam | OK | AWS SAM docs | none |
| awsiotcore | OK | AWS IoT Core MQTT docs | none |
| kxxv2026ctfb | Fixed | YouTube video 8sQmr7JqXkI | real title; the publication date ("July 2026") could not be confirmed and was removed here and in the wiki and docs |
| ctfb2016facility | OK | CTFB press release, 2016 (135,000 sq ft, 7× refrigeration, 21 counties) | none |
| kxxv2026waco | Fixed | KXXV article | author Charles Molineaux; unconfirmed year removed |
| colmap | OK | colmap.github.io | none |
| ctfb_impact_fy24 | Fixed | centraltexasfoodbank.org/media/985/download (74M lb, 26.6M lb produce, 251 agencies, 21 counties) | the old URL did not resolve to the report |
| cfs_processing | OK | CFS processing page (7,000 pallets, 35,000 sq ft, 15 docks) | none |
| ctfb_kitchen_volunteer | Fixed | Edible Austin, "Kitchen Magic", 2017-08-30 (4,200 sq ft kitchen and equipment) | the CTFB volunteer page could not be read (HTTP 403), so it was replaced by this article |
| ctfb_kids_cafe | Fixed | CTFB, 2018-10-04 (about 950 meals a day) | date; the paper now says "2018" |
| ctfb_kitchen_history | Fixed | CTFB, 2025-07-10 (about 2,000 meals a day, summer meal site) | date |
| fa_meal | Fixed | feedingamerica.org about-our-claims ("roughly 1.2 pounds" per meal) | real title and URL |

## Claims outside refs.bib

| Where | Claim | Status | Fix |
|---|---|---|---|
| operations.json, OPERATIONS.md | SFSP and CACFP program split | Removed | no public source ties these programs to the kitchen; replaced with Kids Cafe and summer meal sites, labelled as an estimate |
| operations.json | culinary training "12 weeks" | Fixed | the source says nine weeks |
| operations.json | tour covers named areas | Fixed | the source says "warehouse and distribution operations" |
| operations.json | "kettles" | Fixed | "tilt kettles", as the source says |
| COST_ESTIMATE.md, cost.html | Dragino LPS8N price | Fixed | $163.18 each, $326 for two |
| COST_ESTIMATE.md | Minew G1 and MBT02 prices | Fixed | £58.76 (about $76) and £7.41 at 100, with a DigiKey link |
| COST_ESTIMATE.md | Milesight UG65, Insta360 X4 | Fixed | $467.50 and $559.99 |
| COST_ESTIMATE.md | Milesight EM500-PT100 price | Removed | could not be confirmed |
| COST_ESTIMATE.md | ForkOn €22.70 | Removed | could not be confirmed |
| main.tex cost table | hardware totals | Fixed | $1,850 and $840 with the updated prices |
| PIPELINE.md, main.tex | SPZ "arbitrary units" | Fixed | raw units in the OpenCV convention, converted by the world's metric scale factor (World Labs SPZ docs) |
| CTFB email draft | named contact Tracy Ayrhart and a "deck" at media/1182 | Removed | the file is a JPEG and does not establish the role |
| CTFB email draft | Laura Gougeon | OK | named in the FIM Mini-Summit deck of 14 November 2025 |
| LinkedIn post | "Marble generates splats", "deploy pending" | Fixed | worded as the paper states; deployed at $0.00 billed, GPU trial about $1.34 |

Also re-checked and unchanged: the AWS prices for IoT Core, DynamoDB, Lambda, API Gateway, CloudWatch, CloudFront and Fargate, the Timestream closure, and the TwinMaker entity price. The g5 and g6e list prices, the Isaac Sim license page, the Omniverse particle-fields doc, and the prices for the Dragino LHT65N and LDS02, Pozyx and airpinpoint were checked too.

## Not a public reference

The Marble 2 (Atlas) rate of 10 credits per frame at 1,000 credits per dollar appears only inside the author's World Labs developer account. The public v1 pricing page lists $1 per 1,250 credits. The paper reports it as first-hand account data from the run, not as a cited source.
